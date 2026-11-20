"""Chat without setting up an agent (v0.2): a message with no @mention goes to the
built-in default assistant (Argos's harness on a model connection) and its main
memory; @agent calls that agent, which keeps its own memory."""

import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from fakes import FakeAgent, FakeClassifier, fake_agents
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from argos import argos_tools, runner, services
from argos.config import Settings
from argos.db import make_engine, make_sessionmaker
from argos.harness import ToolContext
from argos.main import create_app
from argos.models import Agent, AgentBackend


@pytest.fixture
def app(settings: Settings) -> Iterator[tuple[TestClient, FakeAgent, FakeAgent]]:
    default, claude = FakeAgent("argos", ["기본", "답"]), FakeAgent("claude", ["C"])
    with TestClient(create_app(settings)) as client:
        client.app.state.runner.adapter_factory = fake_agents(  # type: ignore[attr-defined]
            argos=default, claude=claude
        )
        client.app.state.classifier = FakeClassifier(error="no model")  # type: ignore[attr-defined]
        yield client, default, claude


def project(client: TestClient) -> str:
    return next(
        c["id"]
        for c in client.get("/api/v1/channels").json()["channels"]
        if c["kind"] in ("course", "project")
    )


def reply(client: TestClient, root: str) -> dict[str, Any]:
    deadline = time.time() + 5
    while time.time() < deadline:
        replies: list[dict[str, Any]] = client.get(f"/api/v1/messages/{root}/thread").json()[
            "replies"
        ]
        run: dict[str, Any] = (replies[-1].get("run") or {}) if replies else {}
        if run.get("status") == "done":
            return replies[-1]
        time.sleep(0.02)
    raise AssertionError("no finished reply")


def test_the_default_assistant_is_built_in(app: tuple[TestClient, Any, Any]) -> None:
    client, _, _ = app
    agents = {a["name"]: a for a in client.get("/api/v1/agents").json()}
    default = agents["argos"]
    assert (default["backend"], default["is_builtin"]) == ("native", True)


def test_chat_without_mention_goes_to_the_default_assistant(
    app: tuple[TestClient, FakeAgent, FakeAgent],
) -> None:
    client, default, claude = app
    channel = project(client)
    url = f"/api/v1/channels/{channel}/messages"
    # The channel's own /ask agent does not take over the chat view.
    client.patch(
        f"/api/v1/channels/{channel}",
        json={
            "default_agent_id": next(
                a["id"] for a in client.get("/api/v1/agents").json() if a["name"] == "claude"
            )
        },
    )
    sent = client.post(url, json={"body": "안녕", "agent_session": True}).json()
    answer = reply(client, sent["id"])
    assert (answer["author_id"], answer["body"]) == ("argos", "기본답")
    assert len(default.transcripts) == 1 and claude.transcripts == []


def test_mention_calls_that_agent(app: tuple[TestClient, FakeAgent, FakeAgent]) -> None:
    client, default, claude = app
    url = f"/api/v1/channels/{project(client)}/messages"
    sent = client.post(url, json={"body": "@claude 안녕", "agent_session": True}).json()
    assert reply(client, sent["id"])["author_id"] == "claude"
    assert default.transcripts == [] and len(claude.transcripts) == 1


def test_unconfigured_default_assistant_points_to_settings(settings: Settings) -> None:
    config = settings.model_copy(update={"classifier_model": None, "classifier_title_model": None})
    with TestClient(create_app(config)) as client:
        default = next(a for a in client.get("/api/v1/agents").json() if a["name"] == "argos")
        assert default["available"] is False
        assert "설정 → 에이전트 · 모델" in default["problem"]


def test_default_assistant_model_and_connection(app: tuple[TestClient, Any, Any]) -> None:
    client, _, _ = app
    provider = client.get("/api/v1/providers").json()[0]["id"]
    out = client.put(
        "/api/v1/agents/argos/model", json={"model": "qwen3.5:9b", "provider_id": provider}
    ).json()
    assert (out["model"], out["provider_id"]) == ("qwen3.5:9b", provider)
    assert out["available"] is True
    # Only a harness agent has a connection to pick.
    bad = client.put("/api/v1/agents/claude/model", json={"model": None, "provider_id": provider})
    assert bad.status_code == 422
    missing = client.put("/api/v1/agents/argos/model", json={"model": "x", "provider_id": "nope"})
    assert missing.status_code == 404


# --- memory -------------------------------------------------------------------------


@pytest.fixture
async def maker(settings: Settings) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = make_engine(settings.db_url)
    yield make_sessionmaker(engine)
    await engine.dispose()


def ctx(maker: async_sessionmaker[AsyncSession], agent: str, tmp_path: Path) -> ToolContext:
    return ToolContext(workspace=tmp_path, agent=agent, sessionmaker=maker)


async def test_default_assistant_uses_main_memory_agents_their_own(
    maker: async_sessionmaker[AsyncSession], settings: Settings, tmp_path: Path
) -> None:
    main, claude = ctx(maker, "argos", tmp_path), ctx(maker, "claude", tmp_path)
    await argos_tools.remember(main, "사용자는 아침형 인간")
    await argos_tools.remember(claude, "이 프로젝트는 Rust로 작성")
    assert [m["text"] for m in await argos_tools.recall(main)] == ["사용자는 아침형 인간"]
    assert [m["text"] for m in await argos_tools.recall(claude)] == ["이 프로젝트는 Rust로 작성"]
    assert await argos_tools.recall(main, query="rust") == []
    async with maker() as s:
        rows = await services.list_memories(s, services.MAIN_MEMORY)
        assert [m.scope for m in rows] == ["main"]
        # The context shows each agent only its own memory.
        argos = Agent(name="argos", display_name="Argos", backend=AgentBackend.NATIVE)
        other = Agent(name="claude", display_name="Claude", backend=AgentBackend.CLAUDE_CODE)
        mine = "\n".join(await runner.memory_lines(s, argos, settings))
        theirs = "\n".join(await runner.memory_lines(s, other, settings))
    assert "아침형" in mine and "Rust" not in mine
    assert "Rust" in theirs and "아침형" not in theirs


async def test_empty_memory_is_refused(
    maker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    with pytest.raises(services.InvalidError):
        await argos_tools.remember(ctx(maker, "argos", tmp_path), "   ")
