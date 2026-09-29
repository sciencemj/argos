"""Coding mode threads and agent skills (`/name`) in conversations, with fake agents."""

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fakes import FakeAgent, FakeClassifier, fake_agents
from fastapi.testclient import TestClient

from argos.agents import Turn
from argos.config import Settings
from argos.main import create_app
from argos.models import Agent
from argos.skills import Skill, apply_skill, parse_call


@pytest.fixture
def claude() -> FakeAgent:
    return FakeAgent("claude", ["고쳤어요"])


@pytest.fixture
def local() -> FakeAgent:
    return FakeAgent("local", ["네"])


@pytest.fixture
def roots(tmp_path: Path) -> Path:
    project = tmp_path / "jobs" / "argos"
    project.mkdir(parents=True)
    return tmp_path / "jobs"


@pytest.fixture
def app(
    settings: Settings, roots: Path, claude: FakeAgent, local: FakeAgent
) -> Iterator[TestClient]:
    config = settings.model_copy(update={"default_agent": "claude", "job_roots": [roots]})
    with TestClient(create_app(config)) as client:
        runner = client.app.state.runner  # type: ignore[attr-defined]
        runner.adapter_factory = fake_agents(claude=claude, local=local)

        async def skills(agent: Agent, _settings: Settings) -> list[Skill]:
            return [Skill("review", "코드 리뷰")] if agent.name in ("claude", "local") else []

        runner.skill_lister = skills
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


def wait_calls(agent: FakeAgent, count: int) -> None:
    deadline = time.time() + 5
    while len(agent.coding) < count and time.time() < deadline:
        time.sleep(0.02)
    assert len(agent.coding) >= count


def test_channel_project_folder_must_be_inside_job_roots(app: TestClient, roots: Path) -> None:
    course = channel_id(app, "컴퓨터구조")
    ok = app.patch(f"/api/v1/channels/{course}", json={"workspace_path": "argos"})
    assert ok.status_code == 200 and ok.json()["workspace_path"] == str((roots / "argos").resolve())
    outside = app.patch(f"/api/v1/channels/{course}", json={"workspace_path": "/etc"})
    assert outside.status_code == 422
    missing = app.patch(f"/api/v1/channels/{course}", json={"workspace_path": "nope"})
    assert missing.status_code == 422
    cleared = app.patch(f"/api/v1/channels/{course}", json={"workspace_path": ""})
    assert cleared.json()["workspace_path"] is None


def test_coding_thread_runs_in_project_folder(
    app: TestClient, roots: Path, claude: FakeAgent
) -> None:
    course = channel_id(app, "컴퓨터구조")
    app.patch(f"/api/v1/channels/{course}", json={"workspace_path": "argos"})
    project = (roots / "argos").resolve()

    root = send(app, course, "테스트가 깨져, 고쳐줘", coding=True)  # the channel's agent
    assert root["coding"] is True
    wait_calls(claude, 1)
    assert claude.coding[-1] is True and claude.workspaces[-1] == project

    send(app, course, "로그도 봐줘", thread_root_id=root["id"])  # the thread stays coding
    wait_calls(claude, 2)
    assert claude.coding[-1] is True

    send(app, course, "이제 설명만", thread_root_id=root["id"], coding=False)  # switched off
    wait_calls(claude, 3)
    assert claude.coding[-1] is False and claude.workspaces[-1] is None
    thread = app.get(f"/api/v1/messages/{root['id']}/thread").json()
    assert thread["root"]["coding"] is False


def test_coding_needs_folder_and_coding_agent(app: TestClient) -> None:
    course = channel_id(app, "컴퓨터구조")
    response = app.post(
        f"/api/v1/channels/{course}/messages", json={"body": "고쳐줘", "coding": True}
    )
    assert response.status_code == 422  # no project folder yet
    app.patch(f"/api/v1/channels/{course}", json={"workspace_path": "argos"})
    response = app.post(
        f"/api/v1/channels/{course}/messages", json={"body": "@local 고쳐줘", "coding": True}
    )
    assert response.status_code == 422  # a local model cannot code


def test_skill_call_in_dm_and_thread(app: TestClient, local: FakeAgent) -> None:
    dm = app.post("/api/v1/agents/local/dm").json()["id"]
    send(app, dm, "/review 이 PR 봐줘")
    wait_calls(local, 1)
    turn = local.transcripts[-1][-1]
    assert (turn.skill, turn.args) == ("review", "이 PR 봐줘")

    course = channel_id(app, "컴퓨터구조")
    root = send(app, course, "@local 안녕")
    wait_calls(local, 2)
    send(app, course, "/review", thread_root_id=root["id"])  # not an Argos command: a skill
    wait_calls(local, 3)
    assert local.transcripts[-1][-1].skill == "review"

    assert app.get("/api/v1/agents/local/skills").json() == [
        {"name": "review", "description": "코드 리뷰"}
    ]


def test_apply_skill_only_for_known_skills() -> None:
    skills = [Skill("review")]
    assert parse_call("@claude /review now") == ("review", "now")
    assert apply_skill([Turn("user", "/unknown x")], skills)[-1].skill is None
    marked = apply_skill([Turn("user", "/review x")], skills)[-1]
    assert marked.skill == "review" and "review" in marked.text


async def test_skill_cache_drops_duplicate_names(settings: Settings) -> None:
    from argos.skills import SkillCache

    async def lister(_agent: Agent, _settings: Settings) -> list[Skill]:
        return [Skill("a", "first"), Skill("b"), Skill("a", "second")]

    agent = Agent(name="codex", display_name="Codex", backend="codex")
    skills = await SkillCache(lister)(agent, settings)
    assert [(s.name, s.description) for s in skills] == [("a", "first"), ("b", "")]
