"""Unread marks in the sidebar (PLAN Phase 16b)."""

import asyncio
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fakes import FakeAgent, FakeClassifier, fake_agents
from fastapi.testclient import TestClient

from argos.config import Settings
from argos.main import create_app


@pytest.fixture
def hold() -> asyncio.Event:
    return asyncio.Event()


@pytest.fixture
def local(hold: asyncio.Event) -> FakeAgent:
    return FakeAgent("local", ["답"], hold=hold)


@pytest.fixture
def app(settings: Settings, local: FakeAgent) -> Iterator[TestClient]:
    config = settings.model_copy(update={"default_agent": "local"})
    with TestClient(create_app(config)) as client:
        client.app.state.runner.adapter_factory = fake_agents(local=local)  # type: ignore[attr-defined]
        client.app.state.classifier = FakeClassifier(error="no model")  # type: ignore[attr-defined]
        yield client


def channel_id(client: TestClient, name: str) -> str:
    return next(
        c["id"] for c in client.get("/api/v1/channels").json()["channels"] if c["name"] == name
    )


def unread(client: TestClient) -> dict[str, int]:
    return client.get("/api/v1/channels/unread").json()


def wait_for(check: Any) -> None:
    deadline = time.time() + 5
    while time.time() < deadline:
        if check():
            return
        time.sleep(0.02)
    raise AssertionError("condition never held")


def test_seeded_channels_start_read(app: TestClient) -> None:
    assert unread(app) == {}


def test_agent_answer_counts_once_it_finishes(app: TestClient, hold: asyncio.Event) -> None:
    course = channel_id(app, "컴퓨터구조")
    app.post(f"/api/v1/channels/{course}/messages", json={"body": "/ask 질문"})
    # The user's own message and a reply still streaming are not unread.
    time.sleep(0.1)
    assert course not in unread(app)

    app.portal.call(hold.set)  # type: ignore[union-attr]
    wait_for(lambda: unread(app).get(course) == 1)

    assert app.post(f"/api/v1/channels/{course}/read").status_code == 204
    assert course not in unread(app)


def test_lms_import_marks_course_channel(app: TestClient) -> None:
    token = app.post("/api/v1/lms/connection").json()["token"]
    batch = {
        "account_id": "42",
        "courses": [{"id": "7", "name": "알고리즘"}],
        "items": [
            {
                "id": "11",
                "course_id": "7",
                "kind": "announcement",
                "title": "휴강 안내",
                "text": "다음 주 휴강",
                "url": "https://lms.korea.ac.kr/courses/7/discussion_topics/11",
            }
        ],
    }
    imported = app.post(
        "/api/v1/lms/import", json=batch, headers={"Authorization": f"Bearer {token}"}
    )
    assert imported.status_code == 200, imported.text
    course = channel_id(app, "알고리즘")
    assert unread(app)[course] == 1

    app.post(f"/api/v1/channels/{course}/read")
    assert course not in unread(app)


def test_read_mark_needs_a_channel(app: TestClient) -> None:
    assert app.post("/api/v1/channels/nope/read").status_code == 404
