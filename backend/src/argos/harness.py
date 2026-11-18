"""Argos's own agent harness (v0.2 Phase 1): a provider model (OpenAI-compatible or
Anthropic) in a streaming multi-turn tool loop, with tools from a registry and a
permission gate in front of every call.

Layers, each swappable on its own:
- `ToolDef` / `ToolRegistry`: what the model may call. Core tools and plugins alike go
  through `register()`; a tool's `kind` (read | write | destructive) and the paths it
  touches decide whether it runs on its own or waits for the user, so a plugin gets the
  approval gate without doing anything itself.
- `HarnessLLM`: one model turn, streamed. `OpenAIChatLLM` (chat.completions) and
  `AnthropicLLM` (Messages API) turn the same neutral history into their own format.
- `NativeHarnessAdapter`: the loop. It yields the usual `Token`s plus `ToolCall`,
  `ToolResult` and `ApprovalNeeded`; on an approval it awaits `ApprovalGate.wait`, so the
  run pauses until the user decides (the runner pauses its timeout meanwhile).

Small local models often write broken tool arguments: `repair_json` tries the usual
fixes, and when that fails the model gets the error as the tool result and may retry.
"""

import ast
import asyncio
import json
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast

from anthropic import APIConnectionError as AnthropicConnectionError
from anthropic import AsyncAnthropic
from openai import APIConnectionError, AsyncOpenAI

from argos.agents import (
    NO_VISION,
    AgentEvent,
    AgentUnavailable,
    ApprovalNeeded,
    Failure,
    Media,
    Token,
    ToolCall,
    ToolResult,
    Turn,
    claude_content,
    split_attachments,
)
from argos.attachments import ImagePart, data_url
from argos.models import PermissionMode

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from argos.config import Settings

# --- tools --------------------------------------------------------------------------

type ToolKind = Literal["read", "write", "destructive"]
TOOL_KINDS: tuple[ToolKind, ...] = ("read", "write", "destructive")
TOOL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
MAX_OUTPUT = 20_000  # characters of a tool result the model gets back


@dataclass(frozen=True)
class ToolContext:
    """What a tool handler may know about the run calling it.

    `roots`: every folder file tools may touch at all (the workspace is always one);
    paths outside them are refused even when approved. `sessionmaker` / `settings` /
    `classifier` let Argos tools reach the app's data (None in bare unit tests)."""

    workspace: Path
    agent: str
    run_id: str | None = None
    channel_id: str | None = None
    permission_mode: PermissionMode = PermissionMode.AUTO_EDIT
    roots: tuple[Path, ...] = ()
    backup_dir: Path | None = None  # copies of files before write_file/edit_file change them
    sessionmaker: "async_sessionmaker[AsyncSession] | None" = None
    settings: "Settings | None" = None
    classifier: Any = None

    @property
    def actor(self) -> str:
        return f"agent:{self.agent}"

    def allowed_roots(self) -> tuple[Path, ...]:
        return (self.workspace, *self.roots)


type ToolHandler = Callable[[dict[str, Any], ToolContext], Awaitable[str]]


def _no_paths(args: dict[str, Any]) -> Sequence[str]:
    return ()


@dataclass(frozen=True)
class ToolDef:
    """One tool the model may call.

    `kind` is the risk: read (looks only), write (changes files or Argos data),
    destructive (deletes, runs shell commands). `paths` names the files or folders a call
    would touch; any outside the run's workspace makes the call wait for approval.
    `source` / `plugin` say where the tool came from ("core", or "plugin" and its name)."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema of the arguments object
    kind: ToolKind
    handler: ToolHandler
    source: str = "core"
    plugin: str | None = None
    paths: Callable[[dict[str, Any]], Sequence[str]] = _no_paths
    summarize: Callable[[dict[str, Any]], str] | None = None  # the approval card's line

    def summary(self, args: dict[str, Any]) -> str:
        if self.summarize is not None:
            try:
                return self.summarize(args)
            except Exception:  # a plugin's summary must not stop the approval
                pass
        shown = json.dumps(args, ensure_ascii=False, default=str)
        return f"{self.name} {shown[:300]}"

    def openai_spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def anthropic_spec(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }


class ToolRegistry:
    """Named tools. Core tools and plugins add theirs with `register`; an agent's run
    gets a `subset` (its whitelist) or a `copy`."""

    def __init__(self, tools: Sequence[ToolDef] = ()) -> None:
        self._tools: dict[str, ToolDef] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: ToolDef, *, replace: bool = False) -> ToolDef:
        if not TOOL_NAME.match(tool.name):
            raise ValueError(f"invalid tool name {tool.name!r}")
        if tool.kind not in TOOL_KINDS:
            raise ValueError(f"invalid tool kind {tool.kind!r}")
        if tool.name in self._tools and not replace:
            raise ValueError(f"tool {tool.name!r} is already registered")
        self._tools[tool.name] = tool
        return tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> ToolDef | None:
        return self._tools.get(name)

    def tools(self) -> list[ToolDef]:
        return list(self._tools.values())

    def names(self) -> list[str]:
        return list(self._tools)

    def from_source(self, source: str, plugin: str | None = None) -> list[ToolDef]:
        return [
            t
            for t in self._tools.values()
            if t.source == source and (plugin is None or t.plugin == plugin)
        ]

    def subset(self, names: Sequence[str]) -> "ToolRegistry":
        return ToolRegistry([t for n in names if (t := self._tools.get(n)) is not None])

    def copy(self) -> "ToolRegistry":
        return ToolRegistry(self.tools())

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self) -> Iterator[ToolDef]:
        return iter(self.tools())


# The tools every native run starts from. Stage 1.2 registers the Argos and file/shell
# tools here; plugins call `core_tools.register(...)` the same way.
core_tools = ToolRegistry()


# --- permissions --------------------------------------------------------------------

type Decision = Literal["allow", "ask"]


def inside(path: str | Path, workspace: Path) -> bool:
    """Whether `path` (relative ones are taken from the workspace) stays inside it,
    symlinks and `..` resolved."""
    root = workspace.expanduser().resolve()
    target = Path(path).expanduser()
    if not target.is_absolute():
        target = root / target
    return target.resolve().is_relative_to(root)


def decide(tool: ToolDef, args: dict[str, Any], mode: PermissionMode, workspace: Path) -> Decision:
    """auto_edit: reads and edits inside the workspace run on their own; destructive
    tools and anything outside the workspace wait for the user. ask: only reads inside
    the workspace run on their own."""
    try:
        touched = list(tool.paths(args))
    except Exception:  # cannot tell what it touches: ask
        return "ask"
    if any(not inside(p, workspace) for p in touched):
        return "ask"
    if tool.kind == "read":
        return "allow"
    if tool.kind == "destructive" or mode == PermissionMode.ASK:
        return "ask"
    return "allow"


class ApprovalGate(Protocol):
    """Where a paused call asks the user (the runner backs it with Approval rows)."""

    async def request(self, tool: ToolDef, args: dict[str, Any], summary: str) -> str:
        """Records the request; returns the approval id."""
        ...

    async def wait(self, approval_id: str) -> bool:
        """True once approved, False once rejected."""
        ...


# --- self-healing tool arguments -----------------------------------------------------


class ArgsError(ValueError):
    """Tool arguments that are not (and could not be made) a JSON object."""


FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)
PY_LITERALS = re.compile(r"\b(True|False|None)\b")


def _loads_object(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except ValueError:
        return None
    if isinstance(value, str):  # arguments sent as a JSON string of JSON
        return _loads_object(value)
    return cast(dict[str, Any], value) if isinstance(value, dict) else None


def _close_open(text: str) -> str:
    """Closes an unterminated string and any brackets left open (a cut-off answer)."""
    stack: list[str] = []
    in_string = escaped = False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack:
            stack.pop()
    if in_string:
        text += '"'
    return text + "".join(reversed(stack))


def _literal_object(text: str) -> dict[str, Any] | None:
    """Python-literal form (single quotes, True/False/None, trailing commas) read
    safely with ast.literal_eval; only a dict that is also valid JSON data counts."""
    try:
        value = ast.literal_eval(text)
    except (ValueError, SyntaxError, MemoryError, RecursionError, TypeError):
        return None
    if not isinstance(value, dict):
        return None
    try:
        return json.loads(json.dumps(value))  # drops tuples/sets/bytes and the like
    except (TypeError, ValueError):
        return None


def _normalize(text: str) -> str:
    """JSON-ifies Python-ish text outside string literals only: True/False/None and
    trailing commas are fixed between strings, single-quoted strings become JSON
    strings, and nothing inside a string is touched."""
    out: list[str] = []
    plain: list[str] = []

    def flush() -> None:
        chunk = "".join(plain)
        chunk = PY_LITERALS.sub(
            lambda m: {"True": "true", "False": "false"}.get(m.group(1), "null"), chunk
        )
        out.append(chunk)
        plain.clear()

    i = 0
    while i < len(text):
        ch = text[i]
        if ch not in "\"'":
            plain.append(ch)
            i += 1
            continue
        flush()
        j = i + 1
        content: list[str] = []
        while j < len(text) and text[j] != ch:
            if text[j] == "\\" and j + 1 < len(text):
                content.append(text[j : j + 2])
                j += 2
                continue
            content.append(text[j])
            j += 1
        body = "".join(content)
        if ch == "'":
            body = body.replace("\\'", "'").replace('"', '\\"')
        out.append(f'"{body}"')
        i = j + 1
    flush()
    return _drop_trailing_commas("".join(out))


def _drop_trailing_commas(text: str) -> str:
    """Removes a `,` followed only by whitespace and `}`/`]`, outside strings."""
    result: list[str] = []
    in_string = escaped = False
    for index, ch in enumerate(text):
        if in_string:
            result.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == ",":
            rest = text[index + 1 :].lstrip()
            if rest[:1] in ("}", "]"):
                continue
        result.append(ch)
    return "".join(result)


def repair_json(raw: str | None) -> dict[str, Any]:
    """The arguments object, repairing what small models commonly break: code fences,
    words around the object, trailing commas, single quotes, Python literals and a
    cut-off end. Fixes apply outside string literals only, so a value such as
    "None of 'these'" arrives unchanged. Empty means no arguments. Raises ArgsError
    when nothing works."""
    text = (raw or "").strip()
    if not text:
        return {}
    if (value := _loads_object(text)) is not None:
        return value
    if match := FENCE.match(text):
        text = match.group(1)
    start = text.find("{")
    if start == -1:
        raise ArgsError("arguments are not a JSON object")
    end = text.rfind("}")
    body = text[start : end + 1] if end > start else text[start:]
    if (value := _loads_object(body)) is not None:
        return value
    if (value := _literal_object(body)) is not None:
        return value
    for candidate in (_normalize(body), _normalize(_close_open(text[start:]))):
        if (value := _loads_object(candidate)) is not None:
            return value
    raise ArgsError("arguments are not valid JSON")


# --- neutral history -----------------------------------------------------------------


@dataclass
class PendingCall:
    """A tool call as the model wrote it; `args` once parsed (None: it could not be)."""

    id: str
    name: str
    raw: str = ""
    args: dict[str, Any] | None = None


@dataclass
class HMsg:
    role: Literal["user", "assistant", "tool"]
    text: str = ""
    media: tuple[Media, ...] = ()
    calls: tuple[PendingCall, ...] = ()
    call_id: str = ""  # tool results: the call they answer
    name: str = ""
    is_error: bool = False


@dataclass(frozen=True)
class LLMDone:
    text: str
    calls: list[PendingCall] = field(default_factory=list[PendingCall])


class HarnessLLM(Protocol):
    pdf_native: bool  # PDFs go to the model as files, not as extracted text

    def turn(
        self, system: str, history: list[HMsg], tools: list[ToolDef]
    ) -> AsyncIterator[Token | LLMDone]:
        """Streams the answer's text, then one LLMDone with the whole text and calls."""
        ...


def harness_history(pairs: list[tuple[Turn, list[Media]]], me: str, vision: bool) -> list[HMsg]:
    """The Argos transcript as neutral messages: other agents' words attributed, images
    kept only for a model that sees them."""
    history: list[HMsg] = []
    for turn, media in pairs:
        if turn.speaker == me:
            history.append(HMsg("assistant", turn.text))
            continue
        text = turn.text if turn.speaker == "user" else f"[{turn.speaker}]: {turn.text}"
        kept = tuple(m for m in media if vision or not isinstance(m, ImagePart))
        if len(kept) < len(media):
            text = f"{text}\n\n{NO_VISION}"
        history.append(HMsg("user", text, media=kept))
    return history


def _call_id() -> str:
    return f"call_{uuid.uuid4().hex[:24]}"


# --- OpenAI-compatible -----------------------------------------------------------------


def openai_messages(system: str, history: list[HMsg]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in history:
        if m.role == "tool":
            messages.append({"role": "tool", "tool_call_id": m.call_id, "content": m.text})
        elif m.role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": m.text or None}
            if m.calls:
                entry["tool_calls"] = [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.name, "arguments": c.raw or "{}"},
                    }
                    for c in m.calls
                ]
            messages.append(entry)
        else:
            images = [x for x in m.media if isinstance(x, ImagePart)]
            if images:
                parts: list[dict[str, Any]] = [{"type": "text", "text": m.text}]
                parts += [{"type": "image_url", "image_url": {"url": data_url(x)}} for x in images]
                messages.append({"role": "user", "content": parts})
            else:
                messages.append({"role": "user", "content": m.text})
    return messages


def _unavailable(exc: Exception, unreachable: str | None) -> AgentUnavailable:
    if unreachable is not None and isinstance(exc, APIConnectionError | AnthropicConnectionError):
        return AgentUnavailable(unreachable)
    return AgentUnavailable(f"{type(exc).__name__}: {exc}")


class OpenAIChatLLM:
    """chat.completions streaming with tool calls (OpenAI, Ollama, any compatible)."""

    pdf_native = False

    def __init__(
        self,
        client: AsyncOpenAI,
        model: str,
        extra: dict[str, Any] | None = None,
        unreachable: str | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._extra = extra
        self._unreachable = unreachable

    async def turn(
        self, system: str, history: list[HMsg], tools: list[ToolDef]
    ) -> AsyncIterator[Token | LLMDone]:
        kwargs: dict[str, Any] = {}
        if tools:
            kwargs["tools"] = [t.openai_spec() for t in tools]
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=cast(Any, openai_messages(system, history)),
                stream=True,
                extra_body=self._extra,
                **kwargs,
            )
        except Exception as exc:
            raise _unavailable(exc, self._unreachable) from exc
        text: list[str] = []
        slots: dict[int, PendingCall] = {}
        try:
            async for chunk in response:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    text.append(delta.content)
                    yield Token(delta.content)
                for tc in delta.tool_calls or []:
                    index = tc.index if tc.index is not None else len(slots)  # pyright: ignore[reportUnnecessaryComparison]
                    slot = slots.setdefault(index, PendingCall(id="", name=""))
                    if tc.id:
                        slot.id = tc.id
                    if tc.function is not None:
                        if tc.function.name and not slot.name:
                            slot.name = tc.function.name
                        slot.raw += tc.function.arguments or ""
        except APIConnectionError as exc:
            raise _unavailable(exc, self._unreachable) from exc
        finally:
            await response.close()
        calls = [replace(c, id=c.id or _call_id()) for _, c in sorted(slots.items()) if c.name]
        yield LLMDone("".join(text), calls)


# --- Anthropic ------------------------------------------------------------------------

ANTHROPIC_MAX_TOKENS = 8192


def anthropic_messages(history: list[HMsg]) -> list[dict[str, Any]]:
    """Messages API form: tool results ride in a user message, consecutive same-role
    messages merge, and the conversation starts with the user. Blocking (base64)."""
    out: list[dict[str, Any]] = []

    def add(role: str, blocks: list[dict[str, Any]]) -> None:
        if not blocks:
            return
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": blocks})

    for m in history:
        if m.role == "tool":
            add(
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": m.call_id,
                        "content": m.text,
                        "is_error": m.is_error,
                    }
                ],
            )
        elif m.role == "assistant":
            blocks: list[dict[str, Any]] = []
            if m.text.strip():
                blocks.append({"type": "text", "text": m.text})
            blocks += [
                {"type": "tool_use", "id": c.id, "name": c.name, "input": c.args or {}}
                for c in m.calls
            ]
            add("assistant", blocks)
        else:
            add("user", claude_content(m.text or "(빈 메시지)", list(m.media)))
    if out and out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": [{"type": "text", "text": "(이전 대화)"}]})
    return out


class AnthropicLLM:
    """Messages API streaming with tool use. Tool input arrives as JSON fragments and
    goes through the same repair as everyone else's."""

    pdf_native = True

    def __init__(
        self,
        client: AsyncAnthropic,
        model: str,
        max_tokens: int = ANTHROPIC_MAX_TOKENS,
        unreachable: str | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._max_tokens = max_tokens
        self._unreachable = unreachable

    async def turn(
        self, system: str, history: list[HMsg], tools: list[ToolDef]
    ) -> AsyncIterator[Token | LLMDone]:
        messages = await asyncio.to_thread(anthropic_messages, history)
        kwargs: dict[str, Any] = {}
        if tools:
            kwargs["tools"] = [t.anthropic_spec() for t in tools]
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system,
                messages=cast(Any, messages),
                stream=True,
                **kwargs,
            )
        except Exception as exc:
            raise _unavailable(exc, self._unreachable) from exc
        text: list[str] = []
        blocks: dict[int, PendingCall] = {}
        try:
            async for event in response:
                kind = getattr(event, "type", "")
                if kind == "content_block_start":
                    block: Any = getattr(event, "content_block", None)
                    if getattr(block, "type", "") == "tool_use":
                        call = PendingCall(id=str(block.id), name=str(block.name))
                        start = getattr(block, "input", None)
                        if start:  # input given whole (no deltas follow)
                            call.raw = json.dumps(start)
                        blocks[int(getattr(event, "index", len(blocks)))] = call
                elif kind == "content_block_delta":
                    delta: Any = getattr(event, "delta", None)
                    dtype = getattr(delta, "type", "")
                    if dtype == "text_delta" and (piece := getattr(delta, "text", "")):
                        text.append(piece)
                        yield Token(piece)
                    elif dtype == "input_json_delta":
                        index = int(getattr(event, "index", -1))
                        if index in blocks:
                            blocks[index].raw += getattr(delta, "partial_json", "") or ""
        except AnthropicConnectionError as exc:
            raise _unavailable(exc, self._unreachable) from exc
        finally:
            await response.close()
        yield LLMDone("".join(text), [c for _, c in sorted(blocks.items())])


# --- the loop -------------------------------------------------------------------------

ARGS_RETRY = (
    "오류: 도구 인자를 JSON 객체로 해석하지 못했어요 ({error}). "
    "올바른 JSON 객체로 인자를 다시 써서 같은 도구를 다시 호출하세요."
)


class NativeHarnessAdapter:
    MAX_STEPS = 20  # model turns per run

    def __init__(
        self,
        llm: HarnessLLM,
        name: str,
        registry: ToolRegistry,
        tool_context: ToolContext,
        gate: ApprovalGate | None = None,
        vision: Callable[[], Awaitable[bool]] | None = None,
        max_steps: int = MAX_STEPS,
    ) -> None:
        self._llm = llm
        self._name = name
        self._registry = registry
        self._ctx = tool_context
        self._gate = gate
        self._vision = vision  # None: send images and let the server decide
        self._max_steps = max_steps

    async def stream(
        self, transcript: list[Turn], context: str, session: str
    ) -> AsyncIterator[AgentEvent]:
        pairs = await asyncio.to_thread(
            split_attachments, transcript, pdf_native=self._llm.pdf_native
        )
        sees = True
        if any(isinstance(m, ImagePart) for _, media in pairs for m in media):
            sees = self._vision is None or await self._vision()
        history = harness_history(pairs, self._name, sees)
        tools = self._registry.tools()
        for _ in range(self._max_steps):
            done: LLMDone | None = None
            async for event in self._llm.turn(context, history, tools):
                if isinstance(event, LLMDone):
                    done = event
                else:
                    yield event
            if done is None or not done.calls:
                return
            history.append(HMsg("assistant", done.text, calls=tuple(done.calls)))
            for call in done.calls:
                result: ToolResult | None = None
                async for event in self._call(call):
                    yield event
                    if isinstance(event, ToolResult):
                        result = event
                assert result is not None
                history.append(
                    HMsg(
                        "tool",
                        result.output,
                        call_id=call.id,
                        name=call.name,
                        is_error=result.is_error,
                    )
                )
        yield Failure("도구를 너무 여러 번 불러서 멈췄어요")

    async def _call(self, call: PendingCall) -> AsyncIterator[AgentEvent]:
        """One tool call: ToolCall, maybe ApprovalNeeded, then always one ToolResult."""
        try:
            call.args = repair_json(call.raw)
        except ArgsError as exc:
            yield ToolCall(call.id, call.name, {"_raw": call.raw[:2000]})
            yield ToolResult(call.id, call.name, ARGS_RETRY.format(error=exc), True)
            return
        args = call.args
        yield ToolCall(call.id, call.name, args)
        tool = self._registry.get(call.name)
        if tool is None:
            known = ", ".join(self._registry.names()) or "없음"
            yield ToolResult(
                call.id, call.name, f"오류: 없는 도구예요. 쓸 수 있는 도구: {known}", True
            )
            return
        if decide(tool, args, self._ctx.permission_mode, self._ctx.workspace) == "ask":
            if self._gate is None:
                yield ToolResult(
                    call.id, call.name, "오류: 사용자 승인이 필요한데 승인을 받을 수 없어요", True
                )
                return
            summary = tool.summary(args)
            approval_id = await self._gate.request(tool, args, summary)
            yield ApprovalNeeded(approval_id, tool.name, args, summary)
            if not await self._gate.wait(approval_id):
                yield ToolResult(
                    call.id, call.name, "사용자가 이 실행을 거절했어요. 다른 방법을 찾으세요.", True
                )
                return
        try:
            output = await tool.handler(args, self._ctx)
            is_error = False
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a tool's failure is the model's to handle
            output, is_error = f"오류: {type(exc).__name__}: {exc}", True
        if len(output) > MAX_OUTPUT:
            output = output[:MAX_OUTPUT] + f"\n…(잘림: {len(output) - MAX_OUTPUT}자 더 있음)"
        yield ToolResult(call.id, call.name, output, is_error)


# --- what the runner hands build_adapter ------------------------------------------------


@dataclass(frozen=True)
class HarnessRun:
    """The run a native adapter works for: its approvals and permission mode."""

    run_id: str | None = None
    channel_id: str | None = None
    permission_mode: PermissionMode = PermissionMode.AUTO_EDIT
    gate: ApprovalGate | None = None
    workspace: Path | None = None  # the channel's folder (None: the Argos workspace)
    roots: tuple[Path, ...] = ()  # further folders file tools may reach (with approval)
    sessionmaker: "async_sessionmaker[AsyncSession] | None" = None
    classifier: Any = None


def registry_for(tools: list[str] | None, base: ToolRegistry | None = None) -> ToolRegistry:
    """An agent's tools: its whitelist out of the core ones (None: all of them)."""
    source = core_tools if base is None else base
    return source.copy() if tools is None else source.subset(tools)
