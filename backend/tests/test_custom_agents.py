"""Custom agents (PLAN Phase 10): the form/YAML, @mentions, and the tool whitelist the
MCP server enforces."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

from fakes import FakeAgent, fake_agents
from fastapi.testclient import TestClient
from test_mcp import call, rest

from argos.agents import LLMToolAdapter, Status, Token, Turn

TUTOR = {
    "name": "tutor",
    "display_name": "과목 튜터",
    "avatar": "🎓",
    "backend": "ollama",
    "model": "qwen3",
    "system_prompt": "볼트 노트를 근거로 설명한다.",
    "tools": ["search_notes", "get_course_progress"],
}


def channel_id(client: TestClient, name: str) -> str:
    return next(
        c["id"] for c in client.get("/api/v1/channels").json()["channels"] if c["name"] == name
    )


def test_tool_catalog_marks_kinds(client: TestClient) -> None:
    tools = {t["name"]: t["kind"] for t in client.get("/api/v1/agents/tools").json()}
    assert tools["search_notes"] == "read"
    assert tools["add_task"] == "write"
    assert tools["delete_task"] == "approval"


def test_create_and_mention_a_custom_agent(client: TestClient) -> None:
    course = channel_id(client, "컴퓨터구조")
    created = client.post("/api/v1/agents", json=TUTOR | {"channel_ids": [course]})
    assert created.status_code == 201, created.text
    tutor = created.json()
    assert (tutor["is_builtin"], tutor["tools"], tutor["channel_ids"]) == (
        False, ["search_notes", "get_course_progress"], [course],
    )  # fmt: skip

    fake = fake_agents(tutor=FakeAgent("tutor", ["캐시는 ", "작은 기억장치예요."]))
    client.app.state.runner.adapter_factory = fake  # type: ignore[attr-defined]
    sent = client.post(
        f"/api/v1/channels/{course}/messages", json={"body": "@tutor 캐시가 뭐야?"}
    ).json()
    [reply] = client.get(f"/api/v1/messages/{sent['id']}/thread").json()["replies"]
    assert reply["author_id"] == "tutor"
    ask = client.post(
        f"/api/v1/channels/{course}/messages", json={"body": "/ask 한 줄 요약"}
    ).json()
    [answer] = client.get(f"/api/v1/messages/{ask['id']}/thread").json()["replies"]
    assert answer["author_id"] == "tutor"  # the channel's default agent now


def test_invalid_agents_are_refused(client: TestClient) -> None:
    for body, text in [
        (TUTOR | {"name": "Tutor!"}, "영문 소문자"),
        (TUTOR | {"name": "claude"}, "이미 있는"),
        (TUTOR | {"backend": "hermes"}, "Claude, Codex, 로컬 모델"),
        (TUTOR | {"tools": ["rm_rf"]}, "모르는 도구"),
    ]:
        refused = client.post("/api/v1/agents", json=body)
        assert refused.status_code in (409, 422), refused.text
        assert text in refused.json()["error"]["message"]
    assert client.patch("/api/v1/agents/claude", json={"display_name": "x"}).status_code == 409
    assert client.delete("/api/v1/agents/claude").status_code == 409


def test_yaml_round_trip(client: TestClient) -> None:
    course = channel_id(client, "컴퓨터구조")
    client.post("/api/v1/agents", json=TUTOR | {"channel_ids": [course]})
    exported = client.get("/api/v1/agents/tutor/export")
    assert exported.headers["content-type"].startswith("application/yaml")
    text = exported.text
    assert "backend: llm" in text and "default_channels:\n- '#컴퓨터구조'" in text

    assert client.delete("/api/v1/agents/tutor").status_code == 204
    assert client.get("/api/v1/channels").json()["channels"][0]  # still fine
    imported = client.post("/api/v1/agents/import", json={"yaml": text})
    assert imported.status_code == 201, imported.text
    again = imported.json()
    assert (again["display_name"], again["avatar"], again["channel_ids"]) == (
        "과목 튜터",
        "🎓",
        [course],
    )
    bad = client.post("/api/v1/agents/import", json={"yaml": "name: x\nbackend: gpt"})
    assert bad.status_code == 422


def test_deleting_an_agent_resets_its_channels(client: TestClient) -> None:
    course = channel_id(client, "컴퓨터구조")
    client.post("/api/v1/agents", json=TUTOR | {"channel_ids": [course]})
    client.post("/api/v1/agents/tutor/dm")
    client.delete("/api/v1/agents/tutor")
    channels = client.get("/api/v1/channels").json()["channels"]
    assert next(c for c in channels if c["id"] == course)["default_agent_id"] is None
    assert not any(c["kind"] == "dm" and c["name"] == "dm-tutor" for c in channels)


def test_whitelist_is_enforced_by_the_mcp_server(server: str) -> None:
    rest(server, "POST", "/agents", json=TUTOR)
    progress = call(server, "get_course_progress", {}, agent="tutor")
    assert isinstance(progress, list)  # allowed
    refused = call(server, "add_task", {"title": "몰래", "channel": "컴퓨터구조"}, agent="tutor")
    assert "허용되지 않은 도구" in refused["error"] and "add_task" in refused["error"]
    assert rest(server, "GET", "/tasks") == []  # nothing written
    assert "error" not in call(
        server, "add_task", {"title": "기본은 허용", "channel": "컴퓨터구조"}
    )


# --- local model with tools ----------------------------------------------------------------


class FakeTools:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> list[dict[str, Any]]:
        return [
            {"name": "search_notes", "description": "노트 검색", "parameters": {"type": "object"}},
            {"name": "add_task", "description": "할 일", "parameters": {"type": "object"}},
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        self.calls.append((name, arguments))
        return '[{"title": "캐시 정리"}]'


class FakeCompletions:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        if len(self.requests) == 1:
            call_ = SimpleNamespace(
                id="c1",
                function=SimpleNamespace(name="search_notes", arguments='{"query": "캐시"}'),
            )
            message = SimpleNamespace(
                content=None,
                tool_calls=[call_],
                model_dump=lambda **_kw: {"role": "assistant", "tool_calls": [{"id": "c1"}]},  # pyright: ignore[reportUnknownLambdaType]
            )
        else:
            message = SimpleNamespace(content="노트 '캐시 정리'에 따르면…", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_local_model_calls_only_listed_tools() -> None:
    tools = FakeTools()
    completions = FakeCompletions()

    @asynccontextmanager
    async def connect(url: str) -> AsyncGenerator[FakeTools]:
        yield tools

    client: Any = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    adapter = LLMToolAdapter(
        client, "qwen3", "tutor", "http://x/mcp?agent=tutor", ["search_notes"], connect
    )

    async def run() -> list[Any]:
        return [e async for e in adapter.stream([Turn("user", "캐시?")], "ctx", "s")]

    events = asyncio.run(run())
    assert events == [Status("도구 사용 중: search_notes"), Token("노트 '캐시 정리'에 따르면…")]
    offered = [t["function"]["name"] for t in completions.requests[0]["tools"]]
    assert offered == ["search_notes"]  # add_task is not even offered
    assert tools.calls == [("search_notes", {"query": "캐시"})]
    assert completions.requests[1]["messages"][-1] == {
        "role": "tool", "tool_call_id": "c1", "content": '[{"title": "캐시 정리"}]',
    }  # fmt: skip
