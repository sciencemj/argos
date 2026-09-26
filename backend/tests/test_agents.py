"""Agent routing and streaming with fake adapters (no network, no CLIs)."""

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fakes import FakeAgent, FakeClassifier, fake_agents
from fastapi.testclient import TestClient

from argos.agents import Failure, Status, Token, parse_claude_line, parse_codex_line
from argos.config import Settings
from argos.main import create_app

FIXTURES = Path(__file__).parent / "fixtures" / "agents"


@pytest.fixture
def local() -> FakeAgent:
    return FakeAgent("local", ["안녕", "하세요"])


@pytest.fixture
def claude() -> FakeAgent:
    return FakeAgent("claude", ["Forwarding은 ", "바로 넘겨요."])


@pytest.fixture
def codex() -> FakeAgent:
    return FakeAgent("codex", ["코드로 보면 ", "mux예요."])


@pytest.fixture
def app(
    settings: Settings, local: FakeAgent, claude: FakeAgent, codex: FakeAgent
) -> Iterator[TestClient]:
    config = settings.model_copy(update={"default_agent": "local"})
    with TestClient(create_app(config)) as client:
        client.app.state.runner.adapter_factory = fake_agents(  # type: ignore[attr-defined]
            local=local, claude=claude, codex=codex
        )
        client.app.state.classifier = FakeClassifier(error="no model")  # type: ignore[attr-defined]
        yield client


def channel_id(client: TestClient, name: str) -> str:
    return next(
        c["id"] for c in client.get("/api/v1/channels").json()["channels"] if c["name"] == name
    )


def send(client: TestClient, channel: str, body: str, **extra: Any) -> dict[str, Any]:
    response = client.post(f"/api/v1/channels/{channel}/messages", json={"body": body, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def settle(client: TestClient, channel: str, count: int = 1) -> list[dict[str, Any]]:
    """Polls the feed until `count` agent replies under the newest message finished."""
    deadline = time.time() + 5
    while time.time() < deadline:
        top: list[dict[str, Any]] = client.get(f"/api/v1/channels/{channel}/messages").json()[
            "items"
        ]
        replies: list[dict[str, Any]] = top[-1]["agent_replies"] if top else []
        done = [r for r in replies if r["run"] and r["run"]["status"] != "running"]
        if len(done) >= count:
            return replies
        time.sleep(0.02)
    raise AssertionError("agent runs did not finish")


def test_plain_message_is_capture_not_conversation(app: TestClient, local: FakeAgent) -> None:
    course = channel_id(app, "컴퓨터구조")
    message = send(app, course, "금요일까지 과제2")
    assert message["ref_type"] == "inbox_item"
    time.sleep(0.1)
    assert local.transcripts == []


def test_ask_goes_to_default_agent_and_sticks(app: TestClient, local: FakeAgent) -> None:
    course = channel_id(app, "컴퓨터구조")
    asked = send(app, course, "/ask forwarding이 뭐야")
    [reply] = settle(app, course)
    assert (reply["author_id"], reply["body"]) == ("local", "안녕하세요")
    assert reply["run"]["status"] == "done"
    assert app.get("/api/v1/inbox").json()["items"] == []  # a question is not capture
    assert "#컴퓨터구조 (과목)" in local.contexts[0]

    send(app, course, "좀 더 쉽게", thread_root_id=asked["id"])  # sticky: local answers again
    thread = _wait_thread(app, asked["id"], agent_replies=2)
    assert [r["author_id"] for r in thread["replies"] if r["author_type"] == "agent"] == [
        "local",
        "local",
    ]
    assert [t.text for t in local.transcripts[1]][-1] == "좀 더 쉽게"
    assert local.sessions == [f"argos-thread-{asked['id']}"] * 2  # one conversation per thread


def test_mention_sticks_thread_to_that_agent(app: TestClient, claude: FakeAgent) -> None:
    course = channel_id(app, "컴퓨터구조")
    root = send(app, course, "@Claude forwarding이 뭐야")
    [reply] = settle(app, course)
    assert reply["author_id"] == "claude"
    send(app, course, "예시도", thread_root_id=root["id"])
    thread = _wait_thread(app, root["id"], agent_replies=2)
    assert {r["author_id"] for r in thread["replies"] if r["author_type"] == "agent"} == {"claude"}


def test_two_mentions_stream_in_parallel_without_mixing(
    app: TestClient, claude: FakeAgent, codex: FakeAgent
) -> None:
    course = channel_id(app, "컴퓨터구조")
    with app.websocket_connect("/ws") as ws:
        send(app, course, "@Claude @Codex 차이 설명해줘")
        tokens: dict[str, str] = {}
        finished: set[str] = set()
        while len(finished) < 2:
            event = ws.receive_json()
            if event["type"] == "agent.token":
                tokens[event["data"]["agent_id"]] = (
                    tokens.get(event["data"]["agent_id"], "") + event["data"]["text"]
                )
            if event["type"] == "agent.done":
                finished.add(event["data"]["agent_id"])
                assert {"run_id", "agent_id", "message_id"} <= event["data"].keys()
    assert tokens == {"claude": "Forwarding은 바로 넘겨요.", "codex": "코드로 보면 mux예요."}
    replies = settle(app, course, count=2)
    assert {r["author_id"]: r["body"] for r in replies} == tokens


def test_cancel_stops_the_run(settings: Settings) -> None:
    import asyncio

    hold = asyncio.Event()  # never set: the fake waits after its first token
    slow = FakeAgent("claude", ["첫 줄", "다음 줄"], hold=hold)
    with TestClient(create_app(settings)) as client:
        client.app.state.runner.adapter_factory = fake_agents(claude=slow)  # type: ignore[attr-defined]
        course = channel_id(client, "운영체제")
        with client.websocket_connect("/ws") as ws:
            send(client, course, "@claude 길게 설명해줘")
            run_id = None
            while run_id is None:
                event = ws.receive_json()
                if event["type"] == "agent.token":
                    run_id = event["data"]["run_id"]
            ws.send_json({"type": "run.cancel", "data": {"run_id": run_id}})
            while (event := ws.receive_json())["type"] != "agent.done":
                pass
        assert event["data"]["status"] == "cancelled"
        [reply] = settle(client, course)
        assert (reply["run"]["status"], reply["body"]) == ("cancelled", "첫 줄")


def test_unavailable_agent_shows_error(app: TestClient) -> None:
    course = channel_id(app, "컴퓨터구조")
    send(app, course, "@hermes 안녕")  # no fake registered for hermes
    [reply] = settle(app, course)
    assert reply["run"]["status"] == "error"
    assert "unavailable" in reply["run"]["error"]


def test_dm_conversation(app: TestClient, local: FakeAgent) -> None:
    dm = app.post("/api/v1/agents/local/dm").json()
    assert dm["kind"] == "dm"
    assert app.post("/api/v1/agents/local/dm").json()["id"] == dm["id"]  # reused
    send(app, dm["id"], "오늘 뭐 해야 해?")
    items: list[dict[str, Any]] = []
    deadline = time.time() + 5
    while time.time() < deadline:
        items = app.get(f"/api/v1/channels/{dm['id']}/messages").json()["items"]
        if len(items) == 2 and items[1]["run"]["status"] == "done":
            break
        time.sleep(0.02)
    assert [m["author_type"] for m in items] == ["user", "agent"]  # top-level, chat-like
    assert app.get("/api/v1/inbox").json()["items"] == []


def test_agent_list_and_default_setting(app: TestClient) -> None:
    agents = {a["name"]: a for a in app.get("/api/v1/agents").json()}
    assert set(agents) == {"hermes", "claude", "codex", "local"}
    assert agents["hermes"]["available"] is False  # no API key in tests
    assert app.get("/api/v1/settings/agents").json()["default_agent"] == "local"
    assert app.put("/api/v1/settings/agents", json={"default_agent": "codex"}).status_code == 200
    assert app.put("/api/v1/settings/agents", json={"default_agent": "nobody"}).status_code == 422


def _wait_thread(client: TestClient, root_id: str, agent_replies: int) -> dict[str, Any]:
    deadline = time.time() + 5
    while time.time() < deadline:
        thread = client.get(f"/api/v1/messages/{root_id}/thread").json()
        agents = [r for r in thread["replies"] if r["author_type"] == "agent"]
        if len(agents) >= agent_replies and all(r["run"]["status"] != "running" for r in agents):
            return thread
        time.sleep(0.02)
    raise AssertionError("thread did not settle")


# --- CLI stream parsers against real samples (PLAN §8.4) -------------------------------


def _events(name: str, parse: Any) -> list[Any]:
    lines = (FIXTURES / name).read_text().splitlines()
    return [e for line in lines if (e := parse(json.loads(line))) is not None]


def test_claude_stream_fixture() -> None:
    events = _events("claude_stream.jsonl", parse_claude_line)
    text = "".join(e.text for e in events if isinstance(e, Token))
    assert text.startswith("오늘은") and "마감" in text
    assert Status("도구 사용 중: get_today") in events
    assert not any(isinstance(e, Failure) for e in events)


def test_codex_stream_fixture() -> None:
    events = _events("codex_exec.jsonl", parse_codex_line)
    assert Status("도구 사용 중: get_today") in events
    tokens = [e.text for e in events if isinstance(e, Token)]
    assert len(tokens) == 2 and "마감" in tokens[-1]


def test_parsers_tolerate_unknown_lines() -> None:
    assert parse_claude_line({"type": "something_new"}) is None
    assert parse_codex_line({"type": "item.completed", "item": {"type": "reasoning"}}) is None
    assert parse_claude_line(
        {"type": "result", "is_error": True, "result": "Not logged in"}
    ) == Failure("Not logged in")


async def test_cli_adapter_strips_echoed_name(tmp_path: Path) -> None:
    """A model echoing the transcript format ("[codex] …") must not leak it into the reply."""
    from argos.agents import CLIAdapter, Turn, is_codex_final

    script = tmp_path / "fake-cli"
    message = {
        "type": "item.completed",
        "item": {"type": "agent_message", "text": "[codex] 답이에요"},
    }
    lines = [json.dumps(message, ensure_ascii=False), json.dumps({"type": "turn.completed"})]
    script.write_text("#!/bin/sh\n" + "".join(f"echo '{line}'\n" for line in lines))
    script.chmod(0o755)
    adapter = CLIAdapter([str(script)], tmp_path, parse_codex_line, is_codex_final, "codex")
    events = [e async for e in adapter.stream([Turn("user", "질문")], "context", "s")]
    assert events == [Token("답이에요\n\n")]


async def test_hermes_adapter_streams_fixture_and_names_conversation() -> None:
    """Real Hermes SSE (with tool calls) through the SDK; only new turns are sent."""
    import httpx2
    from openai import AsyncOpenAI

    from argos.agents import HermesResponsesAdapter, Turn

    sent: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        body = (FIXTURES / "hermes_responses.sse").read_bytes()
        return httpx2.Response(200, content=body, headers={"content-type": "text/event-stream"})

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    client = AsyncOpenAI(base_url="http://hermes.test/v1", api_key="k", http_client=http)
    adapter = HermesResponsesAdapter(client, "hermes-agent", "hermes")
    transcript = [
        Turn("user", "첫 질문"),
        Turn("hermes", "첫 답"),
        Turn("claude", "끼어든 답"),
        Turn("user", "오늘 날짜는?"),
    ]
    events = [e async for e in adapter.stream(transcript, "ctx", "argos-thread-1")]

    assert sent[0]["conversation"] == "argos-thread-1"
    assert sent[0]["instructions"] == "ctx"
    assert sent[0]["input"] == "[claude]: 끼어든 답\n[사용자]: 오늘 날짜는?"  # after Hermes spoke
    assert Status("도구 사용 중: execute_code") in events
    text = "".join(e.text for e in events if isinstance(e, Token))
    assert "9월 25일" in text


def _sdk_messages(text: str, error: str | None = None) -> list[Any]:
    from claude_agent_sdk import ResultMessage, StreamEvent

    tool = {
        "type": "content_block_start",
        "content_block": {"type": "tool_use", "name": "mcp__argos__get_today"},
    }
    delta = {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}
    result = ResultMessage(
        subtype="success", duration_ms=1, duration_api_ms=1, is_error=error is not None,
        num_turns=1, session_id="s", result=error,
    )  # fmt: skip
    return [StreamEvent("u1", "s", tool), StreamEvent("u2", "s", delta), result]


@pytest.fixture
def fake_sdk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Replaces the SDK's query/get_session_info; records options and prompts."""
    import argos.agents as agents_module
    from argos.agents import ClaudeSDKAdapter

    sessions: set[str] = set()
    calls: list[tuple[str, Any]] = []

    async def fake_query(*, prompt: str, options: Any) -> Any:
        calls.append((prompt, options))
        sessions.add(options.session_id or options.resume)
        for message in _sdk_messages(f"turn{len(calls)}"):
            yield message

    monkeypatch.setattr(agents_module, "query", fake_query)

    def fake_info(session_id: str, directory: str | None = None) -> str | None:
        return session_id if session_id in sessions else None

    monkeypatch.setattr(agents_module, "get_session_info", fake_info)
    adapter = ClaudeSDKAdapter(
        "claude", "haiku", tmp_path, "http://127.0.0.1:8000/mcp?agent=claude", None
    )
    return adapter, calls


async def test_claude_sdk_session_new_then_resume(fake_sdk: Any) -> None:
    from argos.agents import Turn

    adapter, calls = fake_sdk
    first = [
        e async for e in adapter.stream([Turn("user", "암호는 초록사과")], "ctx", "argos-thread-1")
    ]
    assert first == [Status("도구 사용 중: get_today"), Token("turn1")]
    prompt, options = calls[0]
    assert options.session_id and options.resume is None
    assert "암호는 초록사과" in prompt
    # Isolation: no built-in tools, no user settings/hooks, Argos MCP only, Argos context.
    assert options.tools == [] and options.setting_sources == []
    assert options.allowed_tools == ["mcp__argos"] and options.strict_mcp_config
    assert options.mcp_servers == {
        "argos": {"type": "http", "url": "http://127.0.0.1:8000/mcp?agent=claude"}
    }
    assert options.system_prompt == "ctx"

    transcript = [
        Turn("user", "암호는 초록사과"),
        Turn("claude", "알겠어"),
        Turn("user", "암호는?"),
    ]
    [e async for e in adapter.stream(transcript, "ctx", "argos-thread-1")]
    prompt, options = calls[1]
    assert options.resume == calls[0][1].session_id and options.session_id is None
    assert prompt == "암호는?"  # only what Claude has not seen


def test_claude_sdk_error_result_becomes_failure() -> None:
    from argos.agents import claude_sdk_event

    *_, result = _sdk_messages("x", error="Not logged in")
    assert claude_sdk_event(result) == Failure("Not logged in")


FAKE_APP_SERVER = r"""#!/usr/bin/env python3
import json, os, pathlib, sys
state = pathlib.Path(os.environ["FAKE_CODEX_STATE"])
empty = {"threads": [], "requests": [], "replies": []}
data = json.loads(state.read_text()) if state.exists() else empty
fixture = [json.loads(l) for l in open(os.environ["FAKE_CODEX_FIXTURE"])]
def out(obj): print(json.dumps(obj, ensure_ascii=False), flush=True)
def save():  # atomic, so a reader never sees a half-written file
    tmp = state.with_suffix(".tmp"); tmp.write_text(json.dumps(data, ensure_ascii=False))
    os.replace(tmp, state)
for line in sys.stdin:
    msg = json.loads(line)
    if "method" not in msg:  # the client's answer to our approval request
        data["replies"].append(msg); save(); continue
    data["requests"].append(msg); save()
    method, rid, params = msg["method"], msg.get("id"), msg.get("params", {})
    if method == "initialize":
        out({"id": rid, "result": {}})
    elif method == "thread/start":
        tid = "thread-" + str(len(data["threads"]) + 1); data["threads"].append(tid); save()
        out({"method": "thread/started", "params": {"thread": {"id": tid}}})
        out({"id": rid, "result": {"thread": {"id": tid}}})
    elif method == "thread/resume":
        if params["threadId"] in data["threads"]:
            out({"id": rid, "result": {"thread": {"id": params["threadId"]}}})
        else:
            out({"id": rid, "error": {"code": -32000, "message": "thread not found"}})
    elif method == "turn/start":
        out({"id": rid, "result": {"turn": {"id": "turn-1"}}})
        out({"id": 99, "method": "item/commandExecution/requestApproval", "params": {}})
        for note in fixture:
            method = note.get("method", "")
            if method.startswith(("item/", "turn/")) and method != "turn/started":
                out(note)
"""


@pytest.fixture
def fake_codex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from argos.agents import CodexAppServerAdapter

    script = tmp_path / "codex"
    script.write_text(FAKE_APP_SERVER)
    script.chmod(0o755)
    state = tmp_path / "state.json"
    monkeypatch.setenv("FAKE_CODEX_STATE", str(state))
    monkeypatch.setenv("FAKE_CODEX_FIXTURE", str(FIXTURES / "codex_appserver.jsonl"))

    class Ids:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}

        async def get(self, key: str) -> str | None:
            return self.values.get(key)

        async def set(self, key: str, value: str) -> None:
            self.values[key] = value

    ids = Ids()
    adapter = CodexAppServerAdapter(
        "codex", None, tmp_path, "http://127.0.0.1:8000/mcp?agent=codex", str(script), tmp_path, ids
    )

    def seen() -> dict[str, Any]:
        return json.loads(state.read_text())

    return adapter, ids, seen


async def test_codex_app_server_new_thread_streams_and_refuses_approvals(fake_codex: Any) -> None:
    from argos.agents import Turn

    adapter, ids, seen = fake_codex
    events = [
        e
        async for e in adapter.stream([Turn("user", "오늘 할 일 몇 개?")], "ctx", "argos-thread-1")
    ]
    assert ids.values == {"argos-thread-1": "thread-1"}
    text = "".join(e.text for e in events if isinstance(e, Token))
    assert "확인하겠습니다.\n\nArgos에서 확인한" in text  # two agent messages, separated
    assert Status("도구 사용 중: get_today") in events
    methods = [r["method"] for r in seen()["requests"]]
    assert methods == ["initialize", "initialized", "thread/start", "turn/start"]
    start = seen()["requests"][2]["params"]
    assert (start["sandbox"], start["approvalPolicy"], start["developerInstructions"]) == (
        "read-only", "never", "ctx",
    )  # fmt: skip
    assert seen()["replies"][0]["error"]["message"] == "not supported by Argos"


async def test_codex_app_server_resumes_and_recovers(fake_codex: Any) -> None:
    from argos.agents import Turn

    adapter, ids, seen = fake_codex
    [e async for e in adapter.stream([Turn("user", "첫 질문")], "ctx", "argos-thread-1")]
    transcript = [Turn("user", "첫 질문"), Turn("codex", "첫 답"), Turn("user", "다음 질문")]
    [e async for e in adapter.stream(transcript, "ctx", "argos-thread-1")]
    requests = seen()["requests"]
    assert requests[-2]["method"] == "thread/resume"
    assert requests[-1]["params"]["input"] == [{"type": "text", "text": "다음 질문"}]

    ids.values["argos-thread-2"] = "thread-gone"  # e.g. Codex history was cleared
    [e async for e in adapter.stream(transcript, "ctx", "argos-thread-2")]
    assert [r["method"] for r in seen()["requests"][-3:]] == [
        "thread/resume",
        "thread/start",
        "turn/start",
    ]
    assert ids.values["argos-thread-2"] == "thread-2"
    assert "첫 답" in seen()["requests"][-1]["params"]["input"][0]["text"]  # whole thread again


def test_builtin_agent_models_are_set_in_settings(client: TestClient) -> None:
    claude = client.put("/api/v1/agents/claude/model", json={"model": "claude-sonnet-5"})
    assert claude.status_code == 200 and claude.json()["model"] == "claude-sonnet-5"
    assert (
        client.put("/api/v1/agents/codex/model", json={"model": "gpt-5.5"}).json()["model"]
        == "gpt-5.5"
    )
    back = client.put("/api/v1/agents/claude/model", json={"model": ""}).json()
    assert back["model"] is None  # the CLI's own default again
    assert client.put("/api/v1/agents/hermes/model", json={"model": "x"}).status_code == 422
    assert client.put("/api/v1/agents/nobody/model", json={"model": "x"}).status_code == 404
