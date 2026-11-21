"""Default chat (v0.2): a chat message with no @mention runs Argos's own harness on the
model picked for chat (connection + model + reasoning effort), with the main memory.
It is not an agent: never listed or mentionable. @agent calls that agent, which keeps
its own memory."""

import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from fakes import FakeAgent, FakeClassifier, fake_agents
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from argos import argos_tools, runner, services
from argos.agents import AgentUnavailable, local_target
from argos.config import Settings
from argos.db import make_engine, make_sessionmaker
from argos.harness import ToolContext
from argos.main import create_app
from argos.models import (
    Agent,
    AgentBackend,
    AuthorType,
    Channel,
    ChannelKind,
    Memory,
    Message,
)
from argos.providers import ProviderConfig

GPU = {"name": "GPU", "kind": "ollama", "base_url": "http://192.0.2.10:11434/v1"}


@pytest.fixture
def app(settings: Settings) -> Iterator[tuple[TestClient, FakeAgent, FakeAgent]]:
    config = settings.model_copy(update={"classifier_model": "small:1b"})
    default, claude = FakeAgent("_chat", ["기본", "답"]), FakeAgent("claude", ["C"])
    with TestClient(create_app(config)) as client:
        client.app.state.runner.adapter_factory = fake_agents(  # type: ignore[attr-defined]
            _chat=default, claude=claude
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
    deadline = time.time() + 15
    while time.time() < deadline:
        replies: list[dict[str, Any]] = client.get(f"/api/v1/messages/{root}/thread").json()[
            "replies"
        ]
        run: dict[str, Any] = (replies[-1].get("run") or {}) if replies else {}
        if run.get("status") in ("done", "error"):
            return replies[-1]
        time.sleep(0.02)
    raise AssertionError("no finished reply")


def test_default_chat_is_not_an_agent(app: tuple[TestClient, Any, Any]) -> None:
    client, _, _ = app
    names = [a["name"] for a in client.get("/api/v1/agents").json()]
    assert "_chat" not in names and "argos" not in names
    # A user may name their own agent argos; nobody can take the hidden runner's name.
    made = client.post(
        "/api/v1/agents", json={"name": "argos", "display_name": "Mine", "backend": "claude_code"}
    )
    assert made.status_code == 201
    taken = client.post(
        "/api/v1/agents", json={"name": "_chat", "display_name": "X", "backend": "claude_code"}
    )
    assert taken.status_code == 422


def test_hidden_runner_has_no_agent_model_endpoint(app: tuple[TestClient, Any, Any]) -> None:
    client, _, _ = app
    assert client.put("/api/v1/agents/_chat/model", json={"model": "x"}).status_code == 404
    assert client.put("/api/v1/agents/claude/model", json={"model": None}).status_code == 200


def test_chat_without_mention_runs_on_the_chat_model(
    app: tuple[TestClient, FakeAgent, FakeAgent],
) -> None:
    client, default, claude = app
    channel = project(client)
    claude_id = next(a["id"] for a in client.get("/api/v1/agents").json() if a["name"] == "claude")
    # The channel's /ask agent does not take over the chat view.
    client.patch(f"/api/v1/channels/{channel}", json={"default_agent_id": claude_id})
    sent = client.post(
        f"/api/v1/channels/{channel}/messages", json={"body": "안녕", "agent_session": True}
    ).json()
    answer = reply(client, sent["id"])
    assert (answer["author_id"], answer["body"]) == ("_chat", "기본답")
    assert answer["run"]["model"] == "small:1b"  # the classifier's model, borrowed
    assert len(default.transcripts) == 1 and claude.transcripts == []


def test_mention_calls_that_agent(app: tuple[TestClient, FakeAgent, FakeAgent]) -> None:
    client, default, claude = app
    url = f"/api/v1/channels/{project(client)}/messages"
    sent = client.post(url, json={"body": "@claude 안녕", "agent_session": True}).json()
    assert reply(client, sent["id"])["author_id"] == "claude"
    assert default.transcripts == [] and len(claude.transcripts) == 1


def test_chat_settings_pick_connection_model_and_effort(
    app: tuple[TestClient, Any, Any],
) -> None:
    client, _, _ = app
    start = client.get("/api/v1/settings/chat").json()
    assert (start["model"], start["resolved_model"], start["problem"]) == (None, "small:1b", None)
    gpu = client.post("/api/v1/providers", json=GPU).json()["id"]
    # Another connection does not have the classifier's model: pick one there.
    moved = client.put("/api/v1/settings/chat", json={"provider_id": gpu}).json()
    assert moved["resolved_model"] is None
    assert "GPU 연결에서 쓸 모델을 골라 주세요" in moved["problem"]
    picked = client.put("/api/v1/settings/chat", json={"model": "llama3", "effort": "high"}).json()
    assert (picked["provider_name"], picked["resolved_model"], picked["effort"]) == (
        "GPU",
        "llama3",
        "high",
    )
    assert client.put("/api/v1/settings/chat", json={"effort": "max"}).status_code == 422
    assert client.put("/api/v1/settings/chat", json={"provider_id": "nope"}).status_code == 404
    back = client.put("/api/v1/settings/chat", json={"provider_id": None}).json()
    assert back["resolved_model"] == "small:1b"  # the classifier's connection again


def test_nothing_configured_points_to_settings(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:  # no classifier model either
        problem = client.get("/api/v1/settings/chat").json()["problem"]
        assert "설정 → 기본 채팅 모델" in problem


def test_model_and_effort_reach_the_harness(app: tuple[TestClient, Any, Any]) -> None:
    client, _, _ = app
    seen: list[Any] = []

    def factory(agent: Agent, settings: Settings, *args: Any, **kwargs: Any) -> FakeAgent:
        seen.append((agent.model, kwargs.get("harness")))
        return FakeAgent("_chat", ["ok"])

    client.app.state.runner.adapter_factory = factory  # type: ignore[attr-defined]
    client.put("/api/v1/settings/chat", json={"model": "big:8b", "effort": "low"})
    sent = client.post(
        f"/api/v1/channels/{project(client)}/messages",
        json={"body": "hi", "agent_session": True},
    ).json()
    assert reply(client, sent["id"])["run"]["model"] == "big:8b"
    [(model, harness)] = seen
    assert (model, harness.effort) == ("big:8b", "low")


# --- the provider-model rule ------------------------------------------------------------


def test_local_target_borrows_the_classifier_model_only_on_its_connection(
    settings: Settings,
) -> None:
    home = ProviderConfig(
        "home", "Home", "ollama", "http://127.0.0.1:11434/v1", None, "device", True
    )
    gpu = ProviderConfig(
        "gpu", "GPU", "ollama", "http://192.0.2.10:11434/v1", None, "network", False
    )
    config = settings.model_copy(
        update={"model_providers": (home, gpu), "classifier_model": "small:1b"}
    )
    custom = Agent(name="bot", display_name="Bot", backend=AgentBackend.NATIVE, is_builtin=False)
    assert local_target(custom, config) == (home, "small:1b")  # default = classifier's
    custom.provider_id = "gpu"
    with pytest.raises(AgentUnavailable, match="GPU"):
        local_target(custom, config)
    custom.model = "llama3"
    assert local_target(custom, config) == (gpu, "llama3")
    on_classifier = config.model_copy(update={"classifier_provider_id": "gpu"})
    custom.model = None
    assert local_target(custom, on_classifier) == (gpu, "small:1b")


# --- memory -------------------------------------------------------------------------


@pytest.fixture
async def maker(settings: Settings) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = make_engine(settings.db_url)
    yield make_sessionmaker(engine)
    await engine.dispose()


def ctx(maker: async_sessionmaker[AsyncSession], agent: str, tmp_path: Path) -> ToolContext:
    return ToolContext(workspace=tmp_path, agent=agent, sessionmaker=maker)


async def test_default_chat_uses_main_memory_agents_their_own(
    maker: async_sessionmaker[AsyncSession], settings: Settings, tmp_path: Path
) -> None:
    main, claude = ctx(maker, "_chat", tmp_path), ctx(maker, "claude", tmp_path)
    mine = ctx(maker, "argos", tmp_path)  # a user's agent named argos: its own memory
    await argos_tools.remember(main, "사용자는 아침형 인간")
    await argos_tools.remember(claude, "이 프로젝트는 Rust로 작성")
    await argos_tools.remember(mine, "내 argos 메모")
    assert [m["text"] for m in await argos_tools.recall(main)] == ["사용자는 아침형 인간"]
    assert [m["text"] for m in await argos_tools.recall(claude)] == ["이 프로젝트는 Rust로 작성"]
    assert [m["text"] for m in await argos_tools.recall(mine)] == ["내 argos 메모"]
    assert await argos_tools.recall(main, query="rust") == []
    async with maker() as s:
        assert [m.scope for m in await services.list_memories(s, "main")] == ["main"]
        chat = Agent(name="_chat", display_name="Argos", backend=AgentBackend.NATIVE)
        other = Agent(name="claude", display_name="Claude", backend=AgentBackend.CLAUDE_CODE)
        main_text = "\n".join(await runner.memory_lines(s, chat, settings))
        other_text = "\n".join(await runner.memory_lines(s, other, settings))
    assert "아침형" in main_text and "Rust" not in main_text
    assert "Rust" in other_text and "아침형" not in other_text


async def test_empty_memory_is_refused(
    maker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    with pytest.raises(services.InvalidError):
        await argos_tools.remember(ctx(maker, "_chat", tmp_path), "   ")


async def test_memory_context_keeps_its_header_and_newest_entries(
    maker: async_sessionmaker[AsyncSession], settings: Settings, tmp_path: Path
) -> None:
    main = ctx(maker, "_chat", tmp_path)
    for i in range(30):
        await argos_tools.remember(main, f"{i:02d} " + "x" * 300)
    chat = Agent(name="_chat", display_name="Argos", backend=AgentBackend.NATIVE)
    async with maker() as s:
        lines = await runner.memory_lines(s, chat, settings)
    assert len("\n".join(lines)) <= runner.MEMORY_CONTEXT_LIMIT
    assert "remember" in lines[0] and lines[1] == "기억해 둔 것:"
    assert lines[-1].startswith("- 29 ")  # newest kept, shown last
    assert not any(line.startswith("- 00 ") for line in lines)  # oldest dropped


# --- migrating c34de5a's visible built-in `argos` ---------------------------------------


async def test_legacy_builtin_argos_becomes_the_hidden_runner(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as s:
        legacy = Agent(
            name="argos",
            display_name="Argos",
            backend=AgentBackend.NATIVE,
            is_builtin=True,
            model="qwen3.5:9b",
        )
        s.add(legacy)
        s.add(Memory(scope="main", text="keep me"))
        channel = Channel(name="x", kind=ChannelKind.PROJECT)
        s.add(channel)
        await s.flush()
        s.add(
            Message(
                channel_id=channel.id, body="hi", author_type=AuthorType.AGENT, author_id="argos"
            )
        )
        await services.set_setting_quietly(s, "default_agent", "argos")
        await s.commit()
        legacy_id = legacy.id
        await services.seed_defaults(s, {})
        runner_row = await services.default_assistant(s)
        assert runner_row is not None and runner_row.id == legacy_id  # same row: runs stay
        assert runner_row.model is None
        assert (await services.chat_settings(s))["model"] == "qwen3.5:9b"
        assert [a.name for a in await services.list_agents(s)] == [
            "hermes",
            "claude",
            "codex",
            "local",
        ]
        assert (await s.scalars(select(Memory.scope))).all() == ["main"]
        assert await services.get_setting(s, "default_agent") is None
    async with maker() as fresh:  # committed, not just pending in that session
        assert (await fresh.scalars(select(Message.author_id))).all() == ["_chat"]
