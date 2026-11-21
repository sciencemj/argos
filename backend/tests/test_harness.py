"""The native harness (v0.2 Stage 1.1): tool registry, permission decisions, argument
repair, the two model wire formats, the tool loop, and runs pausing on approvals."""

import asyncio
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest
from fakes import FakeClassifier
from fastapi.testclient import TestClient

from argos import harness, providers, services
from argos.agents import (
    AgentEvent,
    AgentUnavailable,
    ApprovalNeeded,
    Failure,
    Token,
    ToolCall,
    ToolResult,
    Turn,
    build_adapter,
)
from argos.approval_waits import ApprovalWaits
from argos.config import Settings
from argos.harness import (
    AnthropicLLM,
    ArgsError,
    HarnessRun,
    HMsg,
    LLMDone,
    NativeHarnessAdapter,
    OpenAIChatLLM,
    PendingCall,
    ToolContext,
    ToolDef,
    ToolRegistry,
    anthropic_messages,
    decide,
    openai_messages,
    repair_json,
)
from argos.hub import hub
from argos.main import create_app
from argos.models import Agent, AgentBackend, PermissionMode
from argos.providers import ProviderConfig

# --- tools and permissions ---------------------------------------------------------------


async def _echo(args: dict[str, Any], ctx: ToolContext) -> str:
    return f"echo {args}"


def tool(name: str, kind: harness.ToolKind, **extra: Any) -> ToolDef:
    return ToolDef(name, f"{name} tool", {"type": "object"}, kind, _echo, **extra)


def test_registry_register_subset_and_sources() -> None:
    registry = ToolRegistry([tool("read_file", "read")])
    plugin = registry.register(tool("weather", "read", source="plugin", plugin="wx"))
    with pytest.raises(ValueError, match="already registered"):
        registry.register(tool("weather", "write"))
    registry.register(tool("weather", "write", source="plugin", plugin="wx"), replace=True)
    with pytest.raises(ValueError, match="invalid tool name"):
        registry.register(tool("bad name", "read"))
    with pytest.raises(ValueError, match="invalid tool kind"):
        registry.register(tool("odd", "explode"))  # pyright: ignore[reportArgumentType]
    assert registry.names() == ["read_file", "weather"]
    assert registry.get("weather") is not plugin  # replaced
    assert [t.name for t in registry.from_source("plugin", "wx")] == ["weather"]
    assert registry.subset(["weather", "missing"]).names() == ["weather"]
    assert "read_file" in registry and len(registry.copy()) == 2


def test_permission_decisions(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()

    def at(*paths: str) -> Any:
        def touched(_args: dict[str, Any]) -> tuple[str, ...]:
            return paths

        return touched

    auto, ask = PermissionMode.AUTO_EDIT, PermissionMode.ASK
    read_in = tool("read", "read", paths=at("notes/a.md"))
    read_out = tool("read", "read", paths=at(str(tmp_path / "secret.txt")))
    write_in = tool("write", "write", paths=at("a.txt"))
    escape = tool("write", "write", paths=at("../outside.txt"))
    shell = tool("shell", "destructive")
    assert decide(read_in, {}, auto, ws) == "allow"
    assert decide(read_in, {}, ask, ws) == "allow"
    assert decide(read_out, {}, auto, ws) == "ask"  # outside the workspace
    assert decide(write_in, {}, auto, ws) == "allow"
    assert decide(write_in, {}, ask, ws) == "ask"
    assert decide(escape, {}, auto, ws) == "ask"
    assert decide(shell, {}, auto, ws) == "ask"

    def broken(_args: dict[str, Any]) -> list[str]:
        raise KeyError("path")

    assert decide(tool("w", "read", paths=broken), {}, auto, ws) == "ask"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", {}),
        ('{"a": 1}', {"a": 1}),
        ('"{\\"a\\": 1}"', {"a": 1}),  # JSON string of JSON
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('Sure! {"a": 1} hope that helps', {"a": 1}),
        ('{"a": 1,}', {"a": 1}),
        ("{'a': 'x'}", {"a": "x"}),
        ('{"a": True, "b": None}', {"a": True, "b": None}),
        ('{"path": "a.txt", "lines": [1, 2', {"path": "a.txt", "lines": [1, 2]}),
        ('{"text": "cut off', {"text": "cut off"}),
        # Fixes stay outside strings: Python words and commas inside values survive.
        ('{"q": "True or None, ]", "n": None,}', {"q": "True or None, ]", "n": None}),
        ("{'q': 'it\\'s None', 'ok': True}", {"q": "it's None", "ok": True}),
        ("{'a': \"say 'False'\", 'b': [1, 2,],}", {"a": "say 'False'", "b": [1, 2]}),
        ('{"text": "None at all', {"text": "None at all"}),
    ],
)
def test_repair_json(raw: str, expected: dict[str, Any]) -> None:
    assert repair_json(raw) == expected


@pytest.mark.parametrize("raw", ["not json", "[1, 2]", "{nope nope}"])
def test_repair_json_gives_up(raw: str) -> None:
    with pytest.raises(ArgsError):
        repair_json(raw)


# --- wire formats -------------------------------------------------------------------------


HISTORY = [
    HMsg("assistant", "앞선 답"),
    HMsg("user", "파일 읽어줘"),
    HMsg("assistant", "", calls=(PendingCall("c1", "read", '{"p": 1}', {"p": 1}),)),
    HMsg("tool", "내용", call_id="c1", name="read"),
    HMsg("tool", "실패", call_id="c2", name="read", is_error=True),
]


def test_openai_messages() -> None:
    messages = openai_messages("sys", HISTORY)
    assert messages[0] == {"role": "system", "content": "sys"}
    assert messages[3]["tool_calls"][0]["function"] == {"name": "read", "arguments": '{"p": 1}'}
    assert messages[3]["content"] is None
    assert messages[4] == {"role": "tool", "tool_call_id": "c1", "content": "내용"}


def test_anthropic_messages_merge_and_start_with_user() -> None:
    messages = anthropic_messages(HISTORY)
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant", "user"]
    assert messages[3]["content"] == [
        {"type": "tool_use", "id": "c1", "name": "read", "input": {"p": 1}}
    ]
    results = messages[4]["content"]
    assert [(r["tool_use_id"], r["is_error"]) for r in results] == [("c1", False), ("c2", True)]


class FakeStream:
    def __init__(self, items: list[Any]) -> None:
        self.items = items
        self.closed = False

    def __aiter__(self) -> AsyncIterator[Any]:
        async def gen() -> AsyncIterator[Any]:
            for item in self.items:
                yield item

        return gen()

    async def close(self) -> None:
        self.closed = True


def _chunk(content: str | None = None, calls: list[Any] | None = None) -> Any:
    delta = SimpleNamespace(content=content, tool_calls=calls)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def _call_delta(index: int, id: str | None, name: str | None, args: str) -> Any:
    return SimpleNamespace(index=index, id=id, function=SimpleNamespace(name=name, arguments=args))


async def _collect(it: AsyncIterator[Any]) -> list[Any]:
    return [x async for x in it]


async def test_openai_llm_streams_text_and_assembles_calls() -> None:
    stream = FakeStream(
        [
            _chunk("읽어 "),
            _chunk(calls=[_call_delta(0, "c1", "read", '{"pa')]),
            _chunk(calls=[_call_delta(0, None, None, 'th": "a"}')]),
            _chunk(calls=[_call_delta(1, None, "list", "")]),
            SimpleNamespace(choices=[]),
        ]
    )
    seen: dict[str, Any] = {}

    async def create(**kwargs: Any) -> FakeStream:
        seen.update(kwargs)
        return stream

    client: Any = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    llm = OpenAIChatLLM(client, "m")
    events = await _collect(llm.turn("sys", [HMsg("user", "hi")], [tool("read", "read")]))
    assert events[0] == Token("읽어 ")
    done = events[-1]
    assert isinstance(done, LLMDone) and done.text == "읽어 "
    assert [(c.id, c.name, c.raw) for c in done.calls][0] == ("c1", "read", '{"path": "a"}')
    assert done.calls[1].name == "list" and done.calls[1].id.startswith("call_")
    assert seen["tools"][0]["function"]["name"] == "read" and seen["stream"] is True
    assert stream.closed


async def test_anthropic_llm_streams_text_and_tool_input() -> None:
    def ev(kind: str, **fields: Any) -> Any:
        return SimpleNamespace(type=kind, **fields)

    stream = FakeStream(
        [
            ev("message_start"),
            ev("content_block_start", index=0, content_block=SimpleNamespace(type="text")),
            ev(
                "content_block_delta",
                index=0,
                delta=SimpleNamespace(type="text_delta", text="볼게요"),
            ),
            ev(
                "content_block_start",
                index=1,
                content_block=SimpleNamespace(type="tool_use", id="tu1", name="read", input={}),
            ),
            ev(
                "content_block_delta",
                index=1,
                delta=SimpleNamespace(type="input_json_delta", partial_json='{"p":'),
            ),
            ev(
                "content_block_delta",
                index=1,
                delta=SimpleNamespace(type="input_json_delta", partial_json=" 2}"),
            ),
            ev("message_stop"),
        ]
    )
    seen: dict[str, Any] = {}

    async def create(**kwargs: Any) -> FakeStream:
        seen.update(kwargs)
        return stream

    client: Any = SimpleNamespace(messages=SimpleNamespace(create=create))
    llm = AnthropicLLM(client, "claude-x")
    events = await _collect(llm.turn("sys", [HMsg("user", "hi")], [tool("read", "read")]))
    assert events[0] == Token("볼게요")
    done = events[-1]
    assert isinstance(done, LLMDone)
    assert [(c.id, c.name, c.raw) for c in done.calls] == [("tu1", "read", '{"p": 2}')]
    assert seen["system"] == "sys" and seen["tools"][0]["input_schema"] == {"type": "object"}
    assert seen["messages"] == [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]


# --- the loop -----------------------------------------------------------------------------


class ScriptedLLM:
    """Plays one scripted turn per call and remembers the history it was given."""

    pdf_native = False

    def __init__(self, turns: list[LLMDone]) -> None:
        self.turns = turns
        self.histories: list[list[HMsg]] = []

    async def turn(
        self, system: str, history: list[HMsg], tools: list[ToolDef]
    ) -> AsyncIterator[Token | LLMDone]:
        self.histories.append(list(history))
        done = self.turns.pop(0)
        if done.text:
            yield Token(done.text)
        yield done


class FakeGate:
    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.requests: list[tuple[str, dict[str, Any], str]] = []

    async def request(self, tool: ToolDef, args: dict[str, Any], summary: str) -> str:
        self.requests.append((tool.name, args, summary))
        return f"ap{len(self.requests)}"

    async def wait(self, approval_id: str) -> bool:
        return self.answer


def call(id: str, name: str, raw: str) -> PendingCall:
    return PendingCall(id, name, raw)


def adapter(
    llm: ScriptedLLM,
    registry: ToolRegistry,
    tmp_path: Path,
    gate: FakeGate | None = None,
    mode: PermissionMode = PermissionMode.AUTO_EDIT,
) -> NativeHarnessAdapter:
    ctx = ToolContext(workspace=tmp_path, agent="bot", permission_mode=mode)
    return NativeHarnessAdapter(llm, "bot", registry, ctx, gate)


async def run(a: NativeHarnessAdapter) -> list[AgentEvent]:
    return [e async for e in a.stream([Turn("user", "해줘")], "sys", "s")]


async def test_loop_runs_tools_and_feeds_results_back(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            LLMDone("", [call("c1", "echo", '{"x": 1,}')]),  # repaired
            LLMDone("끝났어요"),
        ]
    )
    events = await run(adapter(llm, ToolRegistry([tool("echo", "read")]), tmp_path))
    assert events == [
        ToolCall("c1", "echo", {"x": 1}),
        ToolResult("c1", "echo", "echo {'x': 1}", False),
        Token("끝났어요"),
    ]
    last = llm.histories[-1]
    assert last[-2].calls[0].args == {"x": 1}
    assert (last[-1].role, last[-1].call_id, last[-1].text) == ("tool", "c1", "echo {'x': 1}")


async def test_broken_args_and_unknown_tools_go_back_to_the_model(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            LLMDone("", [call("c1", "echo", "엉망"), call("c2", "nope", "{}")]),
            LLMDone("", [call("c3", "echo", '{"x": 2}')]),  # the retry
            LLMDone("됐어요"),
        ]
    )
    events = await run(adapter(llm, ToolRegistry([tool("echo", "read")]), tmp_path))
    results = [e for e in events if isinstance(e, ToolResult)]
    assert [r.is_error for r in results] == [True, True, False]
    assert "JSON" in results[0].output and "echo" in results[1].output
    assert events[0] == ToolCall("c1", "echo", {"_raw": "엉망"})


async def test_approval_needed_then_approved_or_rejected(tmp_path: Path) -> None:
    ran: list[dict[str, Any]] = []

    async def rm(args: dict[str, Any], ctx: ToolContext) -> str:
        ran.append(args)
        return "deleted"

    registry = ToolRegistry([ToolDef("rm", "delete", {"type": "object"}, "destructive", rm)])
    for answer in (True, False):
        llm = ScriptedLLM([LLMDone("", [call("c1", "rm", '{"p": "a"}')]), LLMDone("ok")])
        gate = FakeGate(answer)
        events = await run(adapter(llm, registry, tmp_path, gate))
        assert events[1] == ApprovalNeeded("ap1", "rm", {"p": "a"}, 'rm {"p": "a"}')
        result = events[2]
        assert isinstance(result, ToolResult) and result.is_error is (not answer)
    assert ran == [{"p": "a"}]  # only the approved run deleted


async def test_without_a_gate_calls_needing_approval_are_refused(tmp_path: Path) -> None:
    llm = ScriptedLLM([LLMDone("", [call("c1", "w", "{}")]), LLMDone("ok")])
    registry = ToolRegistry([tool("w", "write")])
    events = await run(adapter(llm, registry, tmp_path, mode=PermissionMode.ASK))
    result = events[1]
    assert isinstance(result, ToolResult) and result.is_error and "승인" in result.output


async def test_tool_errors_and_step_limit(tmp_path: Path) -> None:
    async def boom(args: dict[str, Any], ctx: ToolContext) -> str:
        raise RuntimeError("disk full")

    registry = ToolRegistry([ToolDef("boom", "x", {"type": "object"}, "read", boom)])
    llm = ScriptedLLM([LLMDone("", [call(f"c{i}", "boom", "{}")]) for i in range(3)])
    ctx = ToolContext(workspace=tmp_path, agent="bot")
    a = NativeHarnessAdapter(llm, "bot", registry, ctx, max_steps=3)
    events = await run(a)
    assert isinstance(events[1], ToolResult) and "disk full" in events[1].output
    assert isinstance(events[-1], Failure)


async def test_approval_waits_poll_and_settle() -> None:
    waits = ApprovalWaits()
    answers: list[bool | None] = [None, None]

    async def decided() -> bool | None:
        return answers.pop(0) if answers else None

    task = asyncio.create_task(waits.wait("a1", decided, poll=10))
    await asyncio.sleep(0)
    assert waits.waiting() == ["a1"]
    waits.settle("a1", True)
    assert await task is True
    assert waits.waiting() == []

    async def already() -> bool | None:
        return False

    assert await waits.wait("a2", already) is False  # decided before anyone waited


# --- providers ------------------------------------------------------------------------------


def anthropic_provider(**changes: Any) -> ProviderConfig:
    fields: dict[str, Any] = {
        "id": "pa",
        "name": "Anthropic",
        "kind": "anthropic",
        "base_url": "https://api.anthropic.test",
        "api_key": "sk-ant",
        "locality": "cloud",
        "is_default": False,
    }
    return ProviderConfig(**(fields | changes))


def test_anthropic_base_url_and_compat_endpoint() -> None:
    assert providers.normalize_base_url("anthropic", "https://api.anthropic.com/v1/") == (
        "https://api.anthropic.com"
    )
    p = anthropic_provider()
    assert providers.openai_base_url(p) == "https://api.anthropic.test/v1"
    assert str(providers.chat_client(p, timeout=1, max_retries=0).base_url).rstrip("/") == (
        "https://api.anthropic.test/v1"
    )
    client = providers.anthropic_client(p, timeout=1, max_retries=0)
    assert client.api_key == "sk-ant"


async def test_list_models_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx2.Request] = []

    def record(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"data": [{"id": "claude-b"}, {"id": "claude-a"}]})

    real = httpx2.AsyncClient
    monkeypatch.setattr(
        providers.httpx2,
        "AsyncClient",
        lambda **kw: real(transport=httpx2.MockTransport(record), **kw),  # pyright: ignore
    )
    models = await providers.list_models(anthropic_provider())
    assert [m.name for m in models] == ["claude-a", "claude-b"]
    assert seen[0].url.path == "/v1/models"
    assert seen[0].headers["x-api-key"] == "sk-ant"
    assert seen[0].headers["anthropic-version"] == providers.ANTHROPIC_VERSION


def _agent(**fields: Any) -> Agent:
    base: dict[str, Any] = {
        "name": "nat",
        "display_name": "Nat",
        "backend": AgentBackend.NATIVE,
        "model": "m",
        "is_builtin": False,
        "tools_json": None,
        "provider_id": "pa",
        "send_images": False,
    }
    return Agent(**(base | fields))


def test_build_adapter_native(settings: Settings) -> None:
    config = providers.with_providers(settings, (anthropic_provider(),))
    adapter = build_adapter(_agent(), config, harness=HarnessRun(run_id="r1"))
    assert isinstance(adapter, NativeHarnessAdapter)
    keyless = providers.with_providers(settings, (anthropic_provider(api_key=None),))
    with pytest.raises(AgentUnavailable, match="API 키"):
        build_adapter(_agent(), keyless)
    # A local-model agent on an Anthropic provider also runs through the harness.
    local = _agent(backend=AgentBackend.OLLAMA)
    assert isinstance(build_adapter(local, config), NativeHarnessAdapter)
    assert isinstance(build_adapter(_agent(provider_id=None), settings), NativeHarnessAdapter)


# --- through the runner and the approvals API ------------------------------------------------


@pytest.fixture
def app(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as client:
        client.app.state.classifier = FakeClassifier(error="no model")  # type: ignore[attr-defined]
        yield client


def _wait(check: Any, timeout: float = 5) -> Any:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if result := check():
            return result
        time.sleep(0.02)
    raise AssertionError("timed out")


def _course(app: TestClient) -> str:
    return next(
        c["id"] for c in app.get("/api/v1/channels").json()["channels"] if c["kind"] == "course"
    )


def _native_app(app: TestClient, llm: ScriptedLLM, registry: ToolRegistry) -> None:
    def factory(
        agent: Agent,
        settings: Settings,
        sessions: Any = None,
        job_workspace: Any = None,
        no_tools: bool = False,
        coding: bool = False,
        harness: HarnessRun | None = None,
    ) -> NativeHarnessAdapter:
        assert harness is not None and harness.gate is not None
        ctx = ToolContext(
            workspace=settings.argos_workspace,
            agent=agent.name,
            run_id=harness.run_id,
            permission_mode=harness.permission_mode,
        )
        return NativeHarnessAdapter(llm, agent.name, registry, ctx, harness.gate)

    app.app.state.runner.adapter_factory = factory  # type: ignore[attr-defined]
    created = app.post(
        "/api/v1/agents",
        json={"name": "nat", "display_name": "Nat", "backend": "native", "model": "m", "tools": []},
    )
    assert created.status_code == 201, created.text


@pytest.mark.parametrize("approve", [True, False])
def test_native_run_pauses_for_approval(
    app: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch, approve: bool
) -> None:
    assert settings.argos_workspace.is_dir()  # made at startup
    deleted: list[str] = []

    async def rm(args: dict[str, Any], ctx: ToolContext) -> str:
        deleted.append(args["path"])
        return "deleted"

    registry = ToolRegistry(
        [
            ToolDef(
                "rm",
                "delete a file",
                {"type": "object"},
                "destructive",
                rm,
                summarize=lambda a: f"파일 삭제: {a['path']}",
            )
        ]
    )
    llm = ScriptedLLM([LLMDone("", [call("c1", "rm", '{"path": "a.txt"}')]), LLMDone("처리했어요")])
    _native_app(app, llm, registry)
    published: list[tuple[str, dict[str, Any]]] = []
    real_publish = hub.publish

    async def publish(kind: str, data: dict[str, Any]) -> None:
        published.append((kind, data))
        await real_publish(kind, data)

    monkeypatch.setattr(hub, "publish", publish)
    channel = _course(app)
    sent = app.post(f"/api/v1/channels/{channel}/messages", json={"body": "@nat a.txt 지워줘"})
    assert sent.status_code == 201, sent.text

    pending = _wait(lambda: app.get("/api/v1/approvals", params={"status": "pending"}).json())
    [approval] = pending
    assert approval["action"] == "tool" and approval["summary"] == "파일 삭제: a.txt"
    assert approval["payload_json"]["args"] == {"path": "a.txt"}
    assert deleted == []  # paused
    verb = "approve" if approve else "reject"
    assert app.post(f"/api/v1/approvals/{approval['id']}/{verb}").status_code == 200

    def finished() -> dict[str, Any] | None:
        thread = app.get(f"/api/v1/messages/{sent.json()['id']}/thread").json()
        replies = [r for r in thread["replies"] if r["run"] and r["run"]["status"] != "running"]
        return replies[0] if replies else None

    reply = _wait(finished)
    assert reply["body"] == "처리했어요" and reply["run"]["status"] == "done"
    assert reply["run"]["permission_mode"] == "auto_edit"
    assert deleted == (["a.txt"] if approve else [])
    steps = app.get(f"/api/v1/runs/{reply['run']['id']}/steps").json()
    assert [s["kind"] for s in steps] == ["tool_call", "approval", "tool_result"]
    assert steps[1]["approval_id"] == approval["id"]
    assert steps[2]["is_error"] is (not approve)
    kinds = [k for k, _ in published if k.startswith("agent.")]
    for kind in ("agent.tool_call", "agent.approval", "agent.tool_result", "agent.done"):
        assert kind in kinds


def test_channel_permission_mode_and_leftover_approvals(app: TestClient) -> None:
    hold = asyncio.Event()

    async def never(args: dict[str, Any], ctx: ToolContext) -> str:
        await hold.wait()
        return "x"

    registry = ToolRegistry([ToolDef("w", "write", {"type": "object"}, "write", never)])
    llm = ScriptedLLM([LLMDone("", [call("c1", "w", "{}")]), LLMDone("끝")])
    _native_app(app, llm, registry)
    channel = _course(app)
    patched = app.patch(f"/api/v1/channels/{channel}", json={"permission_mode": "ask"})
    assert patched.json()["permission_mode"] == "ask"
    sent = app.post(f"/api/v1/channels/{channel}/messages", json={"body": "@nat 써줘"}).json()
    [approval] = _wait(lambda: app.get("/api/v1/approvals", params={"status": "pending"}).json())
    thread = app.get(f"/api/v1/messages/{sent['id']}/thread").json()
    run_id = next(r["run"]["id"] for r in thread["replies"] if r["run"])
    assert app.post(f"/api/v1/runs/{run_id}/cancel").json() == {"cancelled": True}
    gone = _wait(
        lambda: [
            a
            for a in app.get("/api/v1/approvals").json()
            if a["id"] == approval["id"] and a["status"] == "failed"
        ]
    )
    assert gone[0]["error"]


def test_a_job_waiting_for_approval_frees_its_slot(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """job_concurrency=1: a native job paused on an approval must not hold the only
    slot, so the next job runs meanwhile; approving resumes the first."""
    config = settings.model_copy(update={"job_roots": [tmp_path / "jobs"], "job_concurrency": 1})
    ran: list[str] = []

    async def rm(args: dict[str, Any], ctx: ToolContext) -> str:
        ran.append("rm")
        return "deleted"

    registry = ToolRegistry([ToolDef("rm", "delete", {"type": "object"}, "destructive", rm)])
    scripts = [
        ScriptedLLM([LLMDone("", [call("c1", "rm", "{}")]), LLMDone("첫 잡 끝")]),
        ScriptedLLM([LLMDone("둘째 잡 끝")]),
    ]

    def factory(
        agent: Agent,
        settings: Settings,
        sessions: Any = None,
        job_workspace: Any = None,
        no_tools: bool = False,
        coding: bool = False,
        harness: HarnessRun | None = None,
    ) -> NativeHarnessAdapter:
        assert harness is not None
        ctx = ToolContext(workspace=Path(job_workspace), agent=agent.name)
        return NativeHarnessAdapter(scripts.pop(0), agent.name, registry, ctx, harness.gate)

    with TestClient(create_app(config)) as app:
        app.app.state.classifier = FakeClassifier(error="no model")  # type: ignore[attr-defined]
        app.app.state.runner.adapter_factory = factory  # type: ignore[attr-defined]
        created = app.post(
            "/api/v1/agents",
            json={"name": "nat", "display_name": "Nat", "backend": "native", "model": "m"},
        )
        assert created.status_code == 201, created.text
        channel = _course(app)
        url = f"/api/v1/channels/{channel}/messages"
        first = app.post(url, json={"body": "/job @nat 첫 잡"})
        assert first.status_code == 201, first.text
        [approval] = _wait(
            lambda: app.get("/api/v1/approvals", params={"status": "pending"}).json()
        )
        second = app.post(url, json={"body": "/job @nat 둘째 잡"}).json()

        def status(message: dict[str, Any]) -> str:
            return app.get(f"/api/v1/tasks/{message['ref_id']}").json()["job"]["status"]

        _wait(lambda: status(second) == "done")  # ran while the first one waited
        assert status(first.json()) == "running" and ran == []
        assert app.post(f"/api/v1/approvals/{approval['id']}/approve").status_code == 200
        _wait(lambda: status(first.json()) == "done")
        assert ran == ["rm"]


async def test_an_unanswered_approval_times_out(settings: Settings) -> None:
    from argos.db import make_engine, make_sessionmaker
    from argos.runner import _RunApprovals  # pyright: ignore[reportPrivateUsage]

    engine = make_engine(settings.db_url)
    maker = make_sessionmaker(engine)
    async with maker() as s:
        areas = await services.list_areas(s)
        area = (
            areas[0] if areas else await services.create_area(s, name="a", icon=None, actor="user")
        )
        channel = await services.create_channel(
            s, name="c", area_id=area.id, kind=services.ChannelKind.COURSE, actor="user"
        )
    events: list[str] = []

    async def resume() -> None:
        events.append("resume")

    gate = _RunApprovals(
        maker,
        "run-1",
        Agent(name="nat", display_name="Nat", backend=AgentBackend.NATIVE),
        channel.id,
        None,
        pause=lambda: events.append("pause"),
        resume=resume,
        timeout=0.2,
    )
    approval_id = await gate.request(tool("w", "write"), {}, "쓰기")
    assert await gate.wait(approval_id) is False
    assert events == ["pause", "resume"]
    async with maker() as s:
        assert await services.approval_decision(s, approval_id) is False
    await engine.dispose()


async def test_openai_llm_falls_back_to_whole_answers_when_streaming_fails() -> None:
    """W&B Forge answers 500 to stream=True: the turn is asked for whole, and later
    turns skip streaming."""
    import openai

    requests: list[bool] = []
    message = SimpleNamespace(
        content="목록을 볼게요",
        tool_calls=[
            SimpleNamespace(
                id="c9",
                function=SimpleNamespace(name="list_tasks", arguments='{"channel": "알고리즘"}'),
            )
        ],
    )

    async def create(**kwargs: Any) -> Any:
        requests.append(bool(kwargs["stream"]))
        if kwargs["stream"]:
            response = httpx2.Response(500, request=httpx2.Request("POST", "http://forge.test"))
            raise openai.InternalServerError("cannot stream", response=response, body=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    client: Any = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    llm = OpenAIChatLLM(client, "wandb-artifact:///me/p/m:step1")
    events = await _collect(llm.turn("sys", [HMsg("user", "hi")], [tool("list_tasks", "read")]))
    assert events[0] == Token("목록을 볼게요")
    done = events[-1]
    assert isinstance(done, LLMDone)
    assert [(c.id, c.name, c.raw) for c in done.calls] == [
        ("c9", "list_tasks", '{"channel": "알고리즘"}')
    ]
    await _collect(llm.turn("sys", [HMsg("user", "again")], []))
    assert requests == [True, False, False]  # streaming tried once, then skipped


async def test_openai_llm_client_errors_are_not_retried_whole() -> None:
    import openai

    calls: list[bool] = []

    async def create(**kwargs: Any) -> Any:
        calls.append(bool(kwargs["stream"]))
        response = httpx2.Response(401, request=httpx2.Request("POST", "http://forge.test"))
        raise openai.AuthenticationError("bad key", response=response, body=None)

    client: Any = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with pytest.raises(AgentUnavailable):
        await _collect(OpenAIChatLLM(client, "m").turn("sys", [HMsg("user", "hi")], []))
    assert calls == [True]


async def test_precheck_fails_before_any_approval(tmp_path: Path) -> None:
    async def blocked(args: dict[str, Any], ctx: ToolContext) -> None:
        raise services.ConflictError("캘린더 앱에서 고쳐 주세요")

    llm = ScriptedLLM([LLMDone("", [call("c1", "rm", "{}")]), LLMDone("알겠어요")])
    gate = FakeGate(True)
    registry = ToolRegistry([tool("rm", "destructive", precheck=blocked)])
    events = await run(adapter(llm, registry, tmp_path, gate))
    assert ToolResult("c1", "rm", "오류: 캘린더 앱에서 고쳐 주세요", True) in events
    assert gate.requests == []  # no approval was asked for
