"""Coding jobs (PLAN Phase 6) with fake agents: card moves, workspace allowlist, queue."""

import asyncio
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fakes import FakeAgent, fake_agents
from fastapi.testclient import TestClient

from argos import chat, services
from argos.config import Settings
from argos.main import create_app


@pytest.fixture
def job_settings(settings: Settings, tmp_path: Path) -> Settings:
    return settings.model_copy(update={"job_roots": [tmp_path / "jobs"], "job_concurrency": 1})


@pytest.fixture
def codex() -> FakeAgent:
    return FakeAgent(
        "codex",
        ["`alu.v`를 만들어 볼게요.\n\n", "**테스트 4개** 통과 (`test_alu`).\n\n- 남은 일: 없음"],
    )


@pytest.fixture
def app(job_settings: Settings, codex: FakeAgent) -> Iterator[TestClient]:
    with TestClient(create_app(job_settings)) as client:
        client.app.state.runner.adapter_factory = fake_agents(codex=codex)  # type: ignore[attr-defined]
        yield client


def channel_id(client: TestClient, name: str = "컴퓨터구조") -> str:
    channels = client.get("/api/v1/channels").json()["channels"]
    return next(c["id"] for c in channels if c["name"] == name)


def send(client: TestClient, channel: str, body: str) -> Any:
    return client.post(f"/api/v1/channels/{channel}/messages", json={"body": body})


def wait_job(client: TestClient, task_id: str, *statuses: str) -> dict[str, Any]:
    deadline = time.time() + 5
    while time.time() < deadline:
        task = client.get(f"/api/v1/tasks/{task_id}").json()
        if task["job"] and task["job"]["status"] in statuses:
            return task
        time.sleep(0.02)
    raise AssertionError(f"job did not reach {statuses}")


def test_job_moves_card_and_reports_in_thread(
    app: TestClient, codex: FakeAgent, tmp_path: Path
) -> None:
    """PLAN Phase 6 done-criterion: a job runs to the end, the card lands in review and
    the result is in the thread."""
    course = channel_id(app)
    sent = send(app, course, "/job @codex 실습3 ALU 코드 초안 만들어줘")
    assert sent.status_code == 201
    message = sent.json()
    task_id = message["ref_id"]
    assert message["ref_type"] == "task"

    task = wait_job(app, task_id, "done")
    assert task["status"] == "review"
    assert task["job"]["agent"] == "codex"
    workspace = Path(task["job"]["workspace"])
    assert workspace.is_dir() and workspace.is_relative_to((tmp_path / "jobs").resolve())
    assert codex.workspaces == [workspace]
    assert "thinking" in task["job"]["log"]
    # The conclusion as plain text: the last prose paragraph, not the list after it.
    assert task["job"]["summary"] == "테스트 4개 통과 (test_alu)."
    assert task["title"] == "실습3 ALU 코드 초안 만들어줘"

    thread = app.get(f"/api/v1/messages/{message['id']}/thread").json()
    [reply] = thread["replies"]
    assert (
        reply["body"]
        == "`alu.v`를 만들어 볼게요.\n\n**테스트 4개** 통과 (`test_alu`).\n\n- 남은 일: 없음"
    )
    history = [a["action"] for a in app.get(f"/api/v1/tasks/{task_id}/activity").json()]
    assert history.count("moved") == 2  # todo → in_progress → review, by the agent
    actors = {a["actor"] for a in app.get(f"/api/v1/tasks/{task_id}/activity").json()}
    assert "agent:codex" in actors


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("/job @codex 초안 --dir /etc", "허용된 작업 디렉터리 밖"),
        ("/job @codex 초안 --dir ../../outside", "허용된 작업 디렉터리 밖"),
        ("/job @hermes 초안", "@claude나 @codex"),
        ("/job 초안만", "/job @claude"),
    ],
)
def test_bad_jobs_are_refused_before_anything_is_stored(
    app: TestClient, body: str, reason: str
) -> None:
    course = channel_id(app)
    refused = send(app, course, body)
    assert refused.status_code == 422
    assert reason in refused.json()["error"]["message"]
    assert app.get("/api/v1/tasks", params={"channel_id": course}).json() == []


def test_symlink_cannot_escape_the_job_root(tmp_path: Path) -> None:
    root = tmp_path / "jobs"
    root.mkdir()
    (root / "escape").symlink_to(tmp_path)
    with pytest.raises(services.InvalidError):
        services.job_workspace([root], "escape", "x")
    inside = services.job_workspace([root], "lab3", "x")
    assert inside == (root / "lab3").resolve()


def test_second_job_waits_for_a_slot(job_settings: Settings) -> None:
    hold = asyncio.Event()  # never released: the first job keeps its slot
    slow = FakeAgent("codex", ["작업 중"], hold=hold)
    with TestClient(create_app(job_settings)) as client:
        client.app.state.runner.adapter_factory = fake_agents(codex=slow)  # type: ignore[attr-defined]
        course = channel_id(client)
        first = send(client, course, "/job @codex 첫 번째").json()["ref_id"]
        wait_job(client, first, "running")
        second = send(client, course, "/job @codex 두 번째").json()["ref_id"]
        time.sleep(0.2)
        waiting = client.get(f"/api/v1/tasks/{second}").json()
        assert (waiting["job"]["status"], waiting["status"]) == ("queued", "todo")

        client.post(
            f"/api/v1/runs/{client.get(f'/api/v1/tasks/{first}').json()['job']['run_id']}/cancel"
        )
        wait_job(client, first, "cancelled")
        wait_job(client, second, "running")  # the freed slot goes to the queued job


def test_failed_job_stays_in_progress_and_can_run_again(job_settings: Settings) -> None:
    with TestClient(create_app(job_settings)) as client:
        client.app.state.runner.adapter_factory = fake_agents()  # type: ignore[attr-defined]
        course = channel_id(client)
        task_id = send(client, course, "/job @codex 벤치마크 정리").json()["ref_id"]
        failed = wait_job(client, task_id, "error")
        assert failed["status"] == "in_progress"
        assert "unavailable" in failed["job"]["error"]

        codex = FakeAgent("codex", ["다시 해서 됐어요."])
        client.app.state.runner.adapter_factory = fake_agents(codex=codex)  # type: ignore[attr-defined]
        rerun = client.post(
            f"/api/v1/tasks/{task_id}/jobs",
            json={"agent": "codex", "instructions": failed["job"]["instructions"]},
        )
        assert rerun.status_code == 201
        done = wait_job(client, task_id, "done")
        assert done["status"] == "review"
        assert done["job"]["run_id"] != failed["job"]["run_id"]


def test_long_instructions_make_a_short_card_title() -> None:
    long = "fizzbuzz.py와 test_fizzbuzz.py를 만들고 테스트를 실행한 뒤 결과를 두 줄로 요약해줘"
    assert chat.job_title(long) == "fizzbuzz.py와 test_fizzbuzz.py를 만들고 테스트를…"
    assert chat.job_title("짧은 일\n자세한 설명") == "짧은 일"


def test_config_lists_job_roots(app: TestClient, tmp_path: Path) -> None:
    config = app.get("/api/v1/config").json()
    assert config["job_roots"] == [str((tmp_path / "jobs").resolve())]
    assert config["job_concurrency"] == 1
