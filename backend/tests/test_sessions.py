"""Chat-first channels (v0.2 Stage 1.3): a new session talks to the channel's agent, and
the sidebar lists a channel's conversations."""

import time
from collections.abc import Iterator
from typing import Any

import pytest
from fakes import FakeAgent, FakeClassifier, fake_agents
from fastapi.testclient import TestClient

from argos.config import Settings
from argos.main import create_app


@pytest.fixture
def app(settings: Settings) -> Iterator[TestClient]:
    config = settings.model_copy(update={"default_agent": "local"})
    with TestClient(create_app(config)) as client:
        # A chat message with no @mention goes to the built-in default assistant.
        client.app.state.runner.adapter_factory = fake_agents(  # type: ignore[attr-defined]
            _chat=FakeAgent("_chat", ["답", "이에요"]), local=FakeAgent("local", ["L"])
        )
        client.app.state.classifier = FakeClassifier(error="no model")  # type: ignore[attr-defined]
        yield client


def course(app: TestClient) -> str:
    return next(
        c["id"] for c in app.get("/api/v1/channels").json()["channels"] if c["kind"] == "course"
    )


def sessions(app: TestClient, channel: str) -> list[dict[str, Any]]:
    return app.get(f"/api/v1/channels/{channel}/threads").json()


def settle(app: TestClient, channel: str) -> list[dict[str, Any]]:
    deadline = time.time() + 15
    while time.time() < deadline:
        rows = sessions(app, channel)
        if rows and not any(r["active"] for r in rows) and rows[0]["reply_count"]:
            return rows
        time.sleep(0.02)
    raise AssertionError("session did not settle")


def test_agent_session_and_session_list(app: TestClient) -> None:
    channel = course(app)
    url = f"/api/v1/channels/{channel}/messages"
    assert app.post(url, json={"body": "금요일까지 과제"}).status_code == 201  # capture
    assert sessions(app, channel) == []
    first = app.post(url, json={"body": "캐시가 뭐야?\n자세히", "agent_session": True}).json()
    [row] = settle(app, channel)
    assert row["root_message_id"] == first["id"]
    assert (row["title"], row["preview"], row["reply_count"]) == ("캐시가 뭐야?", "답이에요", 1)
    assert row["agent_id"] is not None and row["active"] is False
    # A follow-up in the thread goes to the same agent; the session moves to the top.
    second = app.post(url, json={"body": "두 번째", "agent_session": True}).json()
    settle(app, channel)
    app.post(url, json={"body": "이어서", "thread_root_id": first["id"]})
    deadline = time.time() + 15
    while time.time() < deadline:
        rows = sessions(app, channel)
        if rows[0]["root_message_id"] == first["id"] and rows[0]["reply_count"] == 3:
            break
        time.sleep(0.02)
    assert [r["root_message_id"] for r in sessions(app, channel)] == [first["id"], second["id"]]
    assert app.get("/api/v1/channels/nope/threads").status_code == 404


def test_agent_picker_routes_and_resticks(settings: Settings) -> None:
    config = settings.model_copy(update={"default_agent": "local"})
    local, claude = FakeAgent("local", ["L"]), FakeAgent("claude", ["C"])
    with TestClient(create_app(config)) as app:
        app.app.state.runner.adapter_factory = fake_agents(local=local, claude=claude)  # type: ignore[attr-defined]
        app.app.state.classifier = FakeClassifier(error="no model")  # type: ignore[attr-defined]
        channel = course(app)
        url = f"/api/v1/channels/{channel}/messages"
        first = app.post(url, json={"body": "안녕", "agent_session": True, "agent": "claude"})
        assert first.status_code == 201, first.text
        assert first.json()["sticky_agent_id"] is not None
        settle(app, channel)
        assert len(claude.transcripts) == 1 and local.transcripts == []
        # In the thread, picking another agent answers with it and re-sticks the thread.
        app.post(
            url, json={"body": "이어서", "thread_root_id": first.json()["id"], "agent": "local"}
        )
        deadline = time.time() + 15
        while time.time() < deadline and len(local.transcripts) < 1:
            time.sleep(0.02)
        assert len(local.transcripts) == 1
        root = app.get(f"/api/v1/messages/{first.json()['id']}/thread").json()["root"]
        agents = {a["id"]: a["name"] for a in app.get("/api/v1/agents").json()}
        assert agents[root["sticky_agent_id"]] == "local"
        bad = app.post(url, json={"body": "x", "agent_session": True, "agent": "nobody"})
        assert bad.status_code == 422


def test_personal_space_chat_keeps_sessions_out_of_the_inbox(app: TestClient) -> None:
    channels = app.get("/api/v1/channels").json()["channels"]
    personal = next(c["id"] for c in channels if c["kind"] == "personal")
    url = f"/api/v1/channels/{personal}/messages"
    first = app.post(url, json={"body": "오늘 뭐 하지?", "agent_session": True}).json()
    [row] = settle(app, personal)
    assert row["root_message_id"] == first["id"]
    assert row["preview"] == "답이에요"
    # A conversation, not a capture: nothing lands in the inbox.
    inbox = app.get("/api/v1/inbox").json()
    assert "오늘 뭐 하지?" not in str(inbox)
