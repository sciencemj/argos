"""Agent adapters (PLAN Phase 5): one streaming interface over Hermes, Ollama, Claude Code
and Codex.

Cancellation is task cancellation: the runner cancels the asyncio task driving
`stream()`, and each adapter releases its HTTP stream or kills its subprocess in
`finally`. That replaces a separate `cancel(run_id)` method on every adapter.

The CLI output formats are not a public contract (PLAN P6): their parsers are pure
functions tested against real samples in tests/fixtures/agents, and anything they do
not recognise is ignored rather than failing the run.
"""

import asyncio
import json
import os
import re
import shutil
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    CLINotFoundError,
    ResultMessage,
    StreamEvent,
    get_session_info,
    query,
)
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from argos.config import Settings
from argos.models import Agent, AgentBackend

# --- events ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Token:
    text: str


@dataclass(frozen=True)
class Status:
    text: str  # e.g. "Argos 도구 사용 중: get_today"


@dataclass(frozen=True)
class Failure:
    message: str


AgentEvent = Token | Status | Failure


@dataclass(frozen=True)
class Turn:
    speaker: str  # "user" or an agent name
    text: str
    # A `/name args` skill call (skills.apply_skill): `text` then says to use the skill,
    # for backends that cannot invoke it natively.
    skill: str | None = None
    args: str = ""
    skill_path: str | None = None  # Codex: the skill's SKILL.md


class AgentAdapter(Protocol):
    def stream(
        self, transcript: list[Turn], context: str, session: str
    ) -> AsyncIterator[AgentEvent]:
        """`session` names the conversation (one per Argos thread or DM); adapters whose
        backend keeps its own history use it, the others rebuild from `transcript`."""
        ...


class SessionIds(Protocol):
    """Where an adapter remembers a backend-chosen conversation id per Argos session key
    (the runner backs it with the agent_session table)."""

    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str) -> None: ...


class AgentUnavailable(Exception):
    """Configuration or connection problem shown to the user in the reply bubble."""


def render_transcript(transcript: list[Turn], me: str) -> str:
    """Shared-record form for CLI agents (`[codex]: …`), as the debate design uses."""
    lines = [f"[{'사용자' if t.speaker == 'user' else t.speaker}]: {t.text}" for t in transcript]
    return (
        "\n".join(lines)
        + f"\n\n위 대화에 이어서 [{me}]로서 답하세요. 답에 [{me}] 같은 이름 표시는 붙이지 마세요."
    )


# --- OpenAI-compatible (Hermes, Ollama) ---------------------------------------------------


class OpenAICompatAdapter:
    def __init__(
        self, client: AsyncOpenAI, model: str, name: str, extra: dict[str, Any] | None = None
    ) -> None:
        self._client = client
        self._model = model
        self._name = name
        self._extra = extra or {}

    async def stream(
        self, transcript: list[Turn], context: str, session: str
    ) -> AsyncIterator[AgentEvent]:
        messages: list[ChatCompletionMessageParam] = [{"role": "system", "content": context}]
        for turn in transcript:
            if turn.speaker == self._name:
                messages.append({"role": "assistant", "content": turn.text})
            elif turn.speaker == "user":
                messages.append({"role": "user", "content": turn.text})
            else:  # another agent's words, attributed so the model does not claim them
                messages.append({"role": "user", "content": f"[{turn.speaker}]: {turn.text}"})
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                stream=True,
                extra_body=self._extra,
            )
        except Exception as exc:
            raise AgentUnavailable(f"{type(exc).__name__}: {exc}") from exc
        try:
            async for chunk in response:
                if chunk.choices and (text := chunk.choices[0].delta.content):
                    yield Token(text)
        finally:
            await response.close()


class ToolClient(Protocol):
    """What LLMToolAdapter needs from an MCP client (tests pass a fake)."""

    async def list_tools(self) -> list[dict[str, Any]]: ...
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str: ...


class MCPToolClient:
    """The Argos MCP server over HTTP, as the agent (so its whitelist applies)."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def list_tools(self) -> list[dict[str, Any]]:
        listed = await self._client.list_tools()
        return [
            {"name": t.name, "description": t.description or "", "parameters": t.input_schema}
            for t in listed.tools
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        result = await self._client.call_tool(name, arguments)
        text = "\n".join(getattr(c, "text", "") for c in result.content)
        return f"오류: {text}" if result.is_error else text


@asynccontextmanager
async def mcp_tools(url: str) -> AsyncGenerator[ToolClient]:
    from mcp.client import Client

    async with Client(url) as client:
        yield MCPToolClient(client)


class LLMToolAdapter:
    """A local model (OpenAI-compatible function calling) that may call the Argos tools
    on a custom agent's list (PLAN Phase 10). Tool calls go through the MCP server as
    the agent, so the server's whitelist is the final word. Answers arrive whole."""

    MAX_STEPS = 6

    def __init__(
        self,
        client: AsyncOpenAI,
        model: str,
        name: str,
        mcp_url: str,
        tools: list[str],
        connect: Callable[[str], Any] = mcp_tools,
    ) -> None:
        self._client = client
        self._model = model
        self._name = name
        self._mcp_url = mcp_url
        self._tools = tools
        self._connect = connect

    async def stream(
        self, transcript: list[Turn], context: str, session: str
    ) -> AsyncIterator[AgentEvent]:
        messages: list[dict[str, Any]] = [{"role": "system", "content": context}]
        for turn in transcript:
            role = "assistant" if turn.speaker == self._name else "user"
            text = (
                turn.text
                if turn.speaker in (self._name, "user")
                else f"[{turn.speaker}]: {turn.text}"
            )
            messages.append({"role": role, "content": text})
        async with self._connect(self._mcp_url) as tools:
            specs = [
                {"type": "function", "function": t}
                for t in await tools.list_tools()
                if t["name"] in self._tools
            ]
            for _ in range(self.MAX_STEPS):
                try:
                    response = await self._client.chat.completions.create(
                        model=self._model,
                        messages=cast(Any, messages),
                        tools=cast(Any, specs),
                    )
                except Exception as exc:
                    raise AgentUnavailable(f"{type(exc).__name__}: {exc}") from exc
                message = response.choices[0].message
                calls = message.tool_calls or []
                if not calls:
                    yield Token(message.content or "")
                    return
                messages.append(message.model_dump(exclude_none=True))
                for call in calls:
                    function = getattr(call, "function", None)
                    if function is None:
                        continue
                    yield Status(f"도구 사용 중: {function.name}")
                    try:
                        arguments = json.loads(function.arguments or "{}")
                    except ValueError:
                        arguments = {}
                    result = await tools.call_tool(function.name, arguments)
                    messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
            yield Failure("도구를 너무 여러 번 불러서 멈췄어요")


def _obj(value: Any) -> dict[str, Any]:
    """JSON sub-object or {} (CLI formats are not a contract: missing parts are normal)."""
    return value if isinstance(value, dict) else {}  # pyright: ignore[reportUnknownVariableType]


# --- Hermes (Responses API with named conversations) ---------------------------------------


def parse_hermes_event(kind: str, data: dict[str, Any]) -> AgentEvent | None:
    """One SSE event of Hermes `/v1/responses` streaming."""
    if kind == "response.output_text.delta" and data.get("delta"):
        return Token(str(data["delta"]))
    if kind == "response.output_item.added":
        item = _obj(data.get("item"))
        if item.get("type") == "function_call":
            return Status(f"도구 사용 중: {item.get('name', '')}")
    if kind in ("response.failed", "error"):
        error = _obj(_obj(data.get("response")).get("error")) or _obj(data.get("error"))
        return Failure(str(error.get("message") or "Hermes error"))
    return None


def pending_turns(transcript: list[Turn], me: str) -> list[Turn]:
    """What Hermes has not seen yet: everything after its own last answer. On the first
    call in a thread that is the whole thread, other agents' answers included."""
    last = max((i for i, t in enumerate(transcript) if t.speaker == me), default=-1)
    return transcript[last + 1 :]


def render_new_turns(turns: list[Turn]) -> str:
    """What a backend that keeps its own session gets: the plain text for a single user
    message, speaker-tagged lines when others spoke in between."""
    if len(turns) == 1 and turns[0].speaker == "user":
        return turns[0].text
    return "\n".join(f"[{'사용자' if t.speaker == 'user' else t.speaker}]: {t.text}" for t in turns)


class HermesResponsesAdapter:
    """Hermes keeps the history itself under the `conversation` name, the way it keeps
    a session per Discord/Slack thread; Argos only sends what is new."""

    def __init__(self, client: AsyncOpenAI, model: str, name: str) -> None:
        self._client = client
        self._model = model
        self._name = name

    async def stream(
        self, transcript: list[Turn], context: str, session: str
    ) -> AsyncIterator[AgentEvent]:
        text = render_new_turns(pending_turns(transcript, self._name) or transcript[-1:])
        try:
            response = await self._client.responses.create(
                model=self._model,
                input=text,
                instructions=context,
                stream=True,
                extra_body={"conversation": session},
            )
        except Exception as exc:
            raise AgentUnavailable(f"{type(exc).__name__}: {exc}") from exc
        try:
            async for event in response:
                parsed = parse_hermes_event(event.type, event.model_dump())
                if parsed is not None:
                    yield parsed
        finally:
            await response.close()


# --- Claude Code ---------------------------------------------------------------------------


def parse_claude_line(data: dict[str, Any]) -> AgentEvent | None:
    """One line of `claude -p --output-format stream-json --include-partial-messages`."""
    kind = data.get("type")
    if kind == "stream_event":
        event = _obj(data.get("event"))
        if event.get("type") == "content_block_delta":
            delta = _obj(event.get("delta"))
            if delta.get("type") == "text_delta" and delta.get("text"):
                return Token(delta["text"])
        if event.get("type") == "content_block_start":
            block = _obj(event.get("content_block"))
            if block.get("type") == "tool_use":
                return Status(
                    f"도구 사용 중: {str(block.get('name', '')).removeprefix('mcp__argos__')}"
                )
        return None
    if kind == "result" and data.get("is_error"):
        return Failure(str(data.get("result") or data.get("subtype") or "Claude Code error"))
    return None


def is_claude_final(data: dict[str, Any]) -> bool:
    return data.get("type") == "result"


# --- Codex -----------------------------------------------------------------------------------


def parse_codex_line(data: dict[str, Any]) -> AgentEvent | None:
    """One line of `codex exec --json`. Codex sends whole messages, not token deltas."""
    kind = data.get("type")
    item = _obj(data.get("item"))
    if kind == "item.started" and item.get("type") == "mcp_tool_call":
        return Status(f"도구 사용 중: {item.get('tool', '')}")
    if kind == "item.completed" and item.get("type") == "agent_message" and item.get("text"):
        return Token(str(item["text"]).rstrip() + "\n\n")
    if kind in ("turn.failed", "error"):
        error = data.get("error")
        return Failure(str(_obj(error).get("message") or error or "Codex error"))
    return None


def is_codex_final(data: dict[str, Any]) -> bool:
    return data.get("type") in ("turn.completed", "turn.failed")


# --- subprocess plumbing -----------------------------------------------------------------------


class CLIAdapter:
    """Runs a headless CLI in an empty workspace and turns its JSON lines into events.
    The process is killed once the final line arrives (some CLIs linger on hooks), on
    cancellation, and on errors."""

    def __init__(
        self,
        argv: list[str],
        workspace: Path,
        parse: Any,
        is_final: Any,
        name: str,
    ) -> None:
        self._argv = argv
        self._workspace = workspace
        self._parse = parse
        self._is_final = is_final
        self._name = name

    async def stream(
        self, transcript: list[Turn], context: str, session: str
    ) -> AsyncIterator[AgentEvent]:
        """Stateless: every run gets the context and the whole transcript."""
        prompt = f"{context}\n\n{render_transcript(transcript, self._name)}"
        async for event in self._exec([], prompt):
            yield event

    async def _exec(self, extra: list[str], prompt: str) -> AsyncIterator[AgentEvent]:
        binary = shutil.which(self._argv[0])
        if binary is None:
            raise AgentUnavailable(f"{self._argv[0]} 명령을 찾을 수 없어요")
        self._workspace.mkdir(parents=True, exist_ok=True)
        process = await asyncio.create_subprocess_exec(
            binary,
            *self._argv[1:],
            *extra,
            prompt,
            cwd=self._workspace,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "NO_COLOR": "1"},
            limit=16 * 1024 * 1024,
        )
        assert process.stdout is not None
        finished = False
        started = False  # models sometimes echo the "[name]:" transcript format; drop it once
        try:
            async for raw in process.stdout:
                try:
                    data = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(data, dict):
                    continue
                if (event := self._parse(data)) is not None:
                    if isinstance(event, Token) and not started:
                        text = re.sub(rf"^\s*\[{re.escape(self._name)}\]:?\s*", "", event.text)
                        started = bool(text)
                        if not text:
                            continue
                        event = Token(text)
                    yield event
                if self._is_final(data):
                    finished = True
                    break
            if not finished:
                await process.wait()
                stderr = await process.stderr.read() if process.stderr else b""
                if process.returncode:
                    tail = stderr.decode(errors="replace").strip()[-300:]
                    raise AgentUnavailable(
                        f"{self._argv[0]} 종료 코드 {process.returncode}: {tail}"
                    )
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()


# One namespace for every Argos-made backend session id (deterministic uuid5).
SESSION_NAMESPACE = uuid.UUID("6f1d2a4e-5b0c-4f7e-9a3d-8c2b1e0f4a67")


def claude_sdk_event(message: Any) -> AgentEvent | None:
    """Maps a Claude Agent SDK message to an AgentEvent (StreamEvent carries the raw API
    stream event the CLI parser already understands)."""
    if isinstance(message, StreamEvent):
        return parse_claude_line({"type": "stream_event", "event": message.event})
    if isinstance(message, ResultMessage) and message.is_error:
        detail = message.result or "; ".join(message.errors or []) or message.subtype
        return Failure(str(detail))
    return None


JOB_TOOLS = ["Read", "Write", "Edit", "Glob", "Grep", "Bash"]
# Coding mode: what Claude Code uses day to day. The sandboxed shell's network is
# opened by the sandbox settings.
CODING_TOOLS = [*JOB_TOOLS, "WebFetch", "WebSearch", "TodoWrite", "NotebookEdit", "Agent", "Skill"]
# File-editing tools are pre-approved only inside the workspace (`workspace_rules`);
# with permission mode dontAsk every other edit is refused. Bash writes are held to
# the workspace by the sandbox.
EDITING_TOOLS = {"Write", "Edit", "NotebookEdit"}


def workspace_rules(tools: list[str], workspace: Path) -> list[str]:
    """Allow rules for `tools`, editing only under `workspace` ("//" = absolute path)."""
    return [t for t in tools if t not in EDITING_TOOLS] + [f"Edit(/{workspace}/**)"]


# Chat reads the user's settings only to find their skills; their hooks stay off.
CLAUDE_CHAT_SETTINGS = json.dumps({"disableAllHooks": True})


class ClaudeSDKAdapter:
    """Claude through the official Claude Agent SDK, one session per Argos thread/DM.

    The SDK still runs the Claude Code engine with the user's login, but hands back
    typed messages, and `get_session_info` says whether the thread's session exists,
    so there is no guessing between --session-id and --resume. Chat: no built-in tools
    but the Skill tool, the user's skills without their hooks, only the Argos MCP
    server. A job is confined to its workspace; coding mode (`coding`) works like
    Claude Code itself in the project folder: the user's settings, hooks and skills,
    sandboxed shell with network."""

    def __init__(
        self,
        name: str,
        model: str | None,
        workspace: Path,
        mcp_url: str,
        cli_path: str | None,
        job_budget_usd: float | None = None,
        tools: list[str] | None = None,
        coding: bool = False,
    ) -> None:
        self._coding = coding
        self._tools = tools  # Argos tools it may use; None = all, [] = none (PLAN Phase 10)
        self._name = name
        self._model = model
        self._workspace = workspace
        self._mcp_url = mcp_url
        self._cli_path = cli_path
        self._job_budget = job_budget_usd  # set = coding job in `workspace` (PLAN Phase 6)

    async def stream(
        self, transcript: list[Turn], context: str, session: str
    ) -> AsyncIterator[AgentEvent]:
        self._workspace.mkdir(parents=True, exist_ok=True)
        session_id = str(uuid.uuid5(SESSION_NAMESPACE, f"{self._name}:{session}"))
        exists = (
            await asyncio.to_thread(get_session_info, session_id, str(self._workspace))
        ) is not None
        argos = {"argos": {"type": "http", "url": self._mcp_url}} if self._tools != [] else {}
        allowed = (
            ["mcp__argos"] if self._tools is None else [f"mcp__argos__{t}" for t in self._tools]
        )
        common: dict[str, Any] = {
            "mcp_servers": argos,
            "strict_mcp_config": True,
            "setting_sources": [],  # none of the user's settings, hooks or plugins
            "cwd": str(self._workspace),
            "model": self._model,
            "include_partial_messages": True,
            "resume": session_id if exists else None,
            "session_id": None if exists else session_id,
            "cli_path": self._cli_path,
        }
        # Chat reads the user's settings for their skills only; coding mode all of them.
        chat_common: dict[str, Any] = {
            **common,
            "setting_sources": ["user"],
            "settings": CLAUDE_CHAT_SETTINGS,
        }
        coding_common: dict[str, Any] = {
            **common,
            "setting_sources": ["user", "project", "local"],
        }
        if self._job_budget is None and self._tools == []:  # e.g. a debate turn: text only
            options = ClaudeAgentOptions(
                system_prompt=context, tools=[], allowed_tools=allowed, **common
            )
        elif self._job_budget is None:  # chat: Argos tools and skills, Argos context
            options = ClaudeAgentOptions(
                system_prompt=context,
                tools=["Skill"],
                allowed_tools=allowed,
                skills="all",
                **chat_common,
            )
        elif self._coding:  # coding mode: like Claude Code itself, in the project folder
            options = ClaudeAgentOptions(
                system_prompt={"type": "preset", "preset": "claude_code", "append": context},
                tools={"type": "preset", "preset": "claude_code"},
                allowed_tools=[*workspace_rules(CODING_TOOLS, self._workspace), "mcp__argos"],
                skills="all",
                permission_mode="dontAsk",
                sandbox={
                    "enabled": True,  # Bash is sandboxed to the folder, network allowed
                    "autoAllowBashIfSandboxed": True,
                    "allowUnsandboxedCommands": False,
                    "network": {"allowedDomains": ["*"]},
                },
                max_budget_usd=self._job_budget,
                **coding_common,
            )
        else:  # coding job: file and shell tools, confined to the job's workspace
            options = ClaudeAgentOptions(
                system_prompt={"type": "preset", "preset": "claude_code", "append": context},
                tools=JOB_TOOLS,
                allowed_tools=[*workspace_rules(JOB_TOOLS, self._workspace), "mcp__argos"],
                # Anything not pre-approved is refused: no edits outside the workspace.
                permission_mode="dontAsk",
                sandbox={
                    "enabled": True,  # Bash runs sandboxed: writes stay in the workspace
                    "autoAllowBashIfSandboxed": True,
                    "allowUnsandboxedCommands": False,
                },
                max_budget_usd=self._job_budget,
                **common,
            )
        last = transcript[-1] if transcript else None
        if last is not None and last.skill is not None and self._tools != []:
            prompt = f"/{last.skill} {last.args}".strip()  # Claude Code expands it itself
        elif exists:
            prompt = render_new_turns(pending_turns(transcript, self._name) or transcript[-1:])
        else:
            prompt = render_transcript(transcript, self._name)
        try:
            async for message in query(prompt=prompt, options=options):
                if (event := claude_sdk_event(message)) is not None:
                    yield event
                if isinstance(message, ResultMessage):
                    return
        except CLINotFoundError as exc:
            raise AgentUnavailable("claude 명령을 찾을 수 없어요") from exc
        except ClaudeSDKError as exc:
            raise AgentUnavailable(f"Claude: {exc}") from exc


# --- Codex app-server (JSON-RPC over stdio) ---------------------------------------------------


def parse_codex_notification(method: str, params: dict[str, Any]) -> AgentEvent | None:
    """One `codex app-server` notification (protocol v2, experimental: unknown ones are
    ignored)."""
    if method == "item/agentMessage/delta" and params.get("delta"):
        return Token(str(params["delta"]))
    if method == "item/started":
        item = _obj(params.get("item"))
        if item.get("type") in ("mcpToolCall", "dynamicToolCall"):
            return Status(f"도구 사용 중: {item.get('tool') or item.get('name') or ''}")
        if item.get("type") == "commandExecution":
            return Status(f"명령 실행: {str(item.get('command') or '')[:200]}")
        if item.get("type") == "fileChange":
            changes: list[Any] = item.get("changes") or []
            paths = [str(_obj(c).get("path") or "") for c in changes]
            return Status(f"파일 수정: {', '.join(p for p in paths if p)[:200]}")
    if method == "error" and not params.get("willRetry"):
        return Failure(str(_obj(params.get("error")).get("message") or "Codex error"))
    if method == "turn/completed":
        turn = _obj(params.get("turn"))
        if turn.get("status") == "failed":
            return Failure(str(_obj(turn.get("error")).get("message") or "Codex turn failed"))
    return None


async def _close(process: asyncio.subprocess.Process, grace: float = 2.0) -> None:
    """End the app-server: EOF on stdin lets it finish writing its thread history;
    kill it if it is still there after `grace` seconds."""
    if process.returncode is not None:
        return
    if process.stdin is not None:
        process.stdin.close()
    try:
        await asyncio.wait_for(process.wait(), grace)
    except TimeoutError:
        process.kill()
        await process.wait()


class CodexAppServerAdapter:
    """Codex through `codex app-server`: token streaming and one persistent Codex thread
    per Argos thread/DM (`thread/resume`). A process per run keeps the lifecycle simple;
    the thread itself lives in Argos' own CODEX_HOME. Same isolation as the exec mode:
    read-only sandbox, no shell/browser/computer/image tools, Argos MCP auto-approved,
    any other approval or input request from the server is refused."""

    def __init__(
        self,
        name: str,
        model: str | None,
        workspace: Path,
        mcp_url: str | None,
        binary: str,
        home: Path,
        sessions: SessionIds,
        job: bool = False,
        coding: bool = False,
    ) -> None:
        self._job = job or coding  # shell on, writes allowed inside the workspace
        self._coding = coding  # coding mode: network too
        self._name = name
        self._model = model
        self._workspace = workspace
        self._mcp_url = mcp_url
        self._binary = binary
        self._home = home
        self._sessions = sessions
        self._next_id = 0

    def _argv(self) -> list[str]:
        tools = (  # no Argos tools at all: e.g. a debate turn (PLAN Phase 10)
            []
            if self._mcp_url is None
            else [
                "-c", f'mcp_servers.argos.url="{self._mcp_url}"',
                "-c", 'mcp_servers.argos.default_tools_approval_mode="approve"',
            ]
        )  # fmt: skip
        return [
            self._binary,
            "app-server",
            *tools,
            "-c", f"features.shell_tool={'true' if self._job else 'false'}",
            "-c", "features.browser_use=false",
            "-c", "features.computer_use=false",
            "-c", "features.image_generation=false",
            "-c", "features.in_app_browser=false",
        ]  # fmt: skip

    async def stream(
        self, transcript: list[Turn], context: str, session: str
    ) -> AsyncIterator[AgentEvent]:
        self._workspace.mkdir(parents=True, exist_ok=True)
        process = await asyncio.create_subprocess_exec(
            *self._argv(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, "CODEX_HOME": str(self._home), "NO_COLOR": "1"},
            limit=16 * 1024 * 1024,
        )
        try:
            async for event in self._converse(process, transcript, context, session):
                yield event
        finally:
            await _close(process)

    async def _converse(
        self,
        process: asyncio.subprocess.Process,
        transcript: list[Turn],
        context: str,
        session: str,
    ) -> AsyncIterator[AgentEvent]:
        assert process.stdin is not None and process.stdout is not None
        stdin, stdout = process.stdin, process.stdout
        pending: list[dict[str, Any]] = []  # notifications read while awaiting a response

        async def send(method: str, params: dict[str, Any], *, notify: bool = False) -> int:
            message: dict[str, Any] = {"method": method, "params": params}
            if not notify:
                self._next_id += 1
                message["id"] = self._next_id
            stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode())
            await stdin.drain()
            return self._next_id

        async def read() -> dict[str, Any]:
            line = await stdout.readline()
            if not line:
                raise AgentUnavailable("codex app-server가 예기치 않게 종료됐어요")
            return _obj(json.loads(line))

        async def refuse(message: dict[str, Any]) -> None:
            """Server → client requests (approvals, user input): Argos never grants them."""
            error = {"code": -32601, "message": "not supported by Argos"}
            stdin.write((json.dumps({"id": message["id"], "error": error}) + "\n").encode())
            await stdin.drain()

        async def call(method: str, params: dict[str, Any]) -> dict[str, Any]:
            request_id = await send(method, params)
            while True:
                message = await read()
                if message.get("id") == request_id and "method" not in message:
                    return message
                if "method" in message and "id" in message:
                    await refuse(message)
                elif "method" in message:
                    pending.append(message)

        init = await call("initialize", {"clientInfo": {"name": "argos", "version": "0.1"}})
        if "error" in init:
            raise AgentUnavailable(f"codex app-server: {_obj(init['error']).get('message')}")
        await send("initialized", {}, notify=True)

        common: dict[str, Any] = {
            "cwd": str(self._workspace),
            # Jobs may write inside their workspace only (no network); chat stays read-only.
            "sandbox": "workspace-write" if self._job else "read-only",
            "approvalPolicy": "never",
            "developerInstructions": context,
            "model": self._model,
        }
        thread_id = await self._sessions.get(session)
        resumed = False
        if thread_id:
            reply = await call("thread/resume", {"threadId": thread_id, **common})
            resumed = "error" not in reply  # e.g. the thread was deleted: start over below
        if not resumed:
            reply = await call("thread/start", {**common, "ephemeral": False})
            if "error" in reply:
                raise AgentUnavailable(f"codex thread: {_obj(reply['error']).get('message')}")
            thread_id = str(_obj(_obj(reply.get("result")).get("thread")).get("id"))
            await self._sessions.set(session, thread_id)

        text = (
            render_new_turns(pending_turns(transcript, self._name) or transcript[-1:])
            if resumed
            else render_transcript(transcript, self._name)
        )
        items: list[dict[str, Any]] = [{"type": "text", "text": text}]
        last = transcript[-1] if transcript else None
        if last is not None and last.skill is not None and last.skill_path is not None:
            items.insert(0, {"type": "skill", "name": last.skill, "path": last.skill_path})
        turn: dict[str, Any] = {"threadId": thread_id, "input": items, "effort": "low"}
        if self._coding:
            turn["sandboxPolicy"] = {"type": "workspaceWrite", "networkAccess": True}
        started = await call("turn/start", turn)
        if "error" in started:
            raise AgentUnavailable(f"codex turn: {_obj(started['error']).get('message')}")

        tail = ""  # end of the text so far: consecutive agent messages get a blank line
        while True:
            message = pending.pop(0) if pending else await read()
            method = str(message.get("method") or "")
            if "id" in message and method:
                await refuse(message)
                continue
            params = _obj(message.get("params"))
            if method == "item/started" and _obj(params.get("item")).get("type") == "agentMessage":
                if tail and not tail.endswith("\n\n"):
                    yield Token("\n" if tail.endswith("\n") else "\n\n")
            event = parse_codex_notification(method, params)
            if event is not None:
                if isinstance(event, Token):
                    tail = (tail + event.text)[-2:]
                yield event
            if method == "turn/completed":
                return


def prepare_codex_home(home: Path, auth: Path) -> Path:
    """Argos' CODEX_HOME holds links to the user's login (auth.json), their skills and
    their AGENTS.md; not their config, so sandbox and tools stay Argos' choice."""
    home = home.resolve()
    home.mkdir(parents=True, exist_ok=True)
    if not auth.exists():
        raise AgentUnavailable("Codex 로그인 정보가 없어요 (터미널에서 codex login)")
    for name in ("auth.json", "skills", "AGENTS.md"):
        source, link = auth.parent / name, home / name
        if source.exists() and not link.exists() and not link.is_symlink():
            link.symlink_to(source)
    return home


# --- model choices (custom agent form, PLAN Phase 10) --------------------------------------


@dataclass(frozen=True)
class ModelChoice:
    id: str  # what goes into Agent.model
    label: str
    description: str = ""
    default: bool = False  # what the backend uses when no model is set


async def claude_models(settings: Settings) -> list[ModelChoice]:
    """The models Claude Code offers this login (its own /model list)."""
    cli = shutil.which(settings.claude_bin)
    if cli is None:
        raise AgentUnavailable(f"{settings.claude_bin} 명령을 찾을 수 없어요")
    workspace = settings.agent_workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    options = ClaudeAgentOptions(setting_sources=[], tools=[], cwd=str(workspace), cli_path=cli)
    info: dict[str, Any] = {}
    async with ClaudeSDKClient(options) as client:
        info = await client.get_server_info() or {}
    found: list[ModelChoice] = []
    for raw in cast(list[Any], info.get("models") or []):
        model = _obj(raw)
        value = str(model.get("value") or "")
        if not value or value == "default":
            continue
        found.append(
            ModelChoice(
                value, str(model.get("displayName") or value), str(model.get("description") or "")
            )
        )
    return found


async def codex_models(settings: Settings) -> list[ModelChoice]:
    """The models the signed-in Codex offers (app-server `model/list`)."""
    binary = shutil.which(settings.codex_bin)
    if binary is None:
        raise AgentUnavailable(f"{settings.codex_bin} 명령을 찾을 수 없어요")
    home = prepare_codex_home(settings.codex_home, settings.codex_auth)
    process = await asyncio.create_subprocess_exec(
        binary,
        "app-server",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env={**os.environ, "CODEX_HOME": str(home)},
    )
    assert process.stdin is not None and process.stdout is not None
    hello: dict[str, Any] = {"clientInfo": {"name": "argos", "version": "1"}}
    try:
        messages: list[dict[str, Any]] = [
            {"id": 1, "method": "initialize", "params": hello},
            {"method": "initialized"},
            {"id": 2, "method": "model/list", "params": {}},
        ]
        for message in messages:
            process.stdin.write((json.dumps(message) + "\n").encode())
        await process.stdin.drain()
        async with asyncio.timeout(20):
            while line := await process.stdout.readline():
                reply = _obj(json.loads(line))
                if reply.get("id") != 2:
                    continue
                rows = cast(list[Any], _obj(reply.get("result")).get("data") or [])
                return [
                    ModelChoice(
                        str(m.get("id")),
                        str(m.get("displayName") or m.get("id")),
                        str(m.get("description") or ""),
                        bool(m.get("isDefault")),
                    )
                    for m in map(_obj, rows)
                    if m.get("id") and not m.get("hidden")
                ]
        raise AgentUnavailable("Codex가 모델 목록을 주지 않았어요")
    except TimeoutError as exc:
        raise AgentUnavailable("Codex 모델 목록을 가져오지 못했어요") from exc
    finally:
        await _close(process)


# --- factory --------------------------------------------------------------------------------------


class _NoSessions:
    async def get(self, key: str) -> str | None:
        return None

    async def set(self, key: str, value: str) -> None:
        return None


def build_adapter(
    agent: Agent,
    settings: Settings,
    sessions: SessionIds | None = None,
    job_workspace: Path | None = None,
    no_tools: bool = False,
    coding: bool = False,
) -> AgentAdapter:
    """`job_workspace` set: a coding job (PLAN Phase 6) with write access there only;
    with `coding`, a coding-mode thread in that project folder (network, user settings).
    `no_tools`: plain conversation without Argos tools (debate turns, PLAN Phase 10).
    Custom agents get only the Argos tools on their list."""
    if job_workspace is not None and agent.backend not in (
        AgentBackend.CLAUDE_CODE,
        AgentBackend.CODEX,
    ):
        raise AgentUnavailable("코딩 잡은 Claude나 Codex에게만 맡길 수 있어요")
    workspace = settings.agent_workspace.resolve()
    mcp = f"{settings.mcp_url}?agent={agent.name}"
    tools: list[str] | None = [] if no_tools else agent.tools_json
    match agent.backend:
        case AgentBackend.HERMES:
            if settings.hermes_api_key is None:
                raise AgentUnavailable(
                    "Hermes API 키를 찾지 못했어요 "
                    "(~/.hermes/.env의 API_SERVER_KEY, docs/hermes-setup.md)"
                )
            client = AsyncOpenAI(
                base_url=settings.hermes_base_url,
                api_key=settings.hermes_api_key.get_secret_value(),
                timeout=settings.agent_timeout,
                max_retries=0,
            )
            return HermesResponsesAdapter(client, agent.model or settings.hermes_model, agent.name)
        case AgentBackend.OLLAMA:
            # The title model is a chat model; the classifier may be a decision model.
            model = agent.model or settings.classifier_title_model or settings.classifier_model
            if not model:
                raise AgentUnavailable("로컬 모델이 정해지지 않았어요 (설정 → 인박스 분류)")
            base = settings.classifier_base_url or "http://127.0.0.1:11434/v1"
            client = AsyncOpenAI(
                base_url=base, api_key="ollama", timeout=settings.agent_timeout, max_retries=0
            )
            if tools:  # a custom agent with tools: the model calls them through MCP
                return LLMToolAdapter(client, model, agent.name, mcp, tools)
            return OpenAICompatAdapter(
                client, model, agent.name, extra={"reasoning_effort": "none"}
            )
        case AgentBackend.CLAUDE_CODE:
            cli = shutil.which(settings.claude_bin)
            if cli is None:
                raise AgentUnavailable(f"{settings.claude_bin} 명령을 찾을 수 없어요")
            if job_workspace is not None:
                return ClaudeSDKAdapter(
                    agent.name,
                    agent.model,
                    job_workspace,
                    mcp,
                    cli,
                    settings.job_max_budget_usd,
                    coding=coding,
                )
            return ClaudeSDKAdapter(agent.name, agent.model, workspace, mcp, cli, tools=tools)
        case AgentBackend.CODEX:
            binary = shutil.which(settings.codex_bin)
            if binary is None:
                raise AgentUnavailable(f"{settings.codex_bin} 명령을 찾을 수 없어요")
            if settings.codex_mode == "app-server":
                home = prepare_codex_home(settings.codex_home, settings.codex_auth)
                return CodexAppServerAdapter(
                    agent.name,
                    agent.model,
                    job_workspace or workspace,
                    None if tools == [] else mcp,
                    binary,
                    home,
                    sessions or _NoSessions(),
                    job=job_workspace is not None,
                    coding=coding and job_workspace is not None,
                )
            if job_workspace is not None:
                raise AgentUnavailable("코딩 잡은 codex app-server 모드에서만 실행돼요")
            argv = [
                settings.codex_bin,
                "exec",
                "--json",
                "--ephemeral",
                "--skip-git-repo-check",
                "--sandbox", "read-only",
                "-C", str(workspace),
                "--ignore-user-config",
                "-c", 'approval_policy="never"',
                "-c", f'mcp_servers.argos.url="{mcp}"',
                "-c", 'mcp_servers.argos.default_tools_approval_mode="approve"',
                "-c", "features.shell_tool=false",
                "-c", "features.browser_use=false",
                "-c", "features.computer_use=false",
                "-c", "features.image_generation=false",
                "-c", "features.in_app_browser=false",
                "-c", 'model_reasoning_effort="low"',
            ]  # fmt: skip
            if agent.model:
                argv += ["--model", agent.model]
            return CLIAdapter(argv, workspace, parse_codex_line, is_codex_final, agent.name)
    raise AgentUnavailable(f"unknown agent backend {agent.backend!r}")
