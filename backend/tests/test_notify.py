"""Phase 11: notices (once each), messenger delivery, the weekly review, numbers, the
briefing, backups and the launchd agent — with fakes for Hermes and launchd."""

import plistlib
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from argos import notify, ops
from argos.config import Settings
from argos.main import create_app


@pytest.fixture
def app(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as client:
        yield client


def course(client: TestClient) -> str:
    channels = client.get("/api/v1/channels").json()["channels"]
    return next(c["id"] for c in channels if c["name"] == "컴퓨터구조")


def task(
    client: TestClient, title: str, due: datetime | None = None, **extra: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {"channel_id": course(client), "title": title, **extra}
    if due is not None:
        body["due_at"] = due.isoformat()
    return client.post("/api/v1/tasks", json=body).json()


def run(client: TestClient, now: datetime) -> list[Any]:
    notifier = client.app.state.notifier  # type: ignore[attr-defined]
    return client.portal.call(notifier.run, now)  # type: ignore[union-attr]


def titles(client: TestClient) -> list[str]:
    return [n["title"] for n in client.get("/api/v1/notifications").json()["items"]]


def test_deadlines_are_announced_once_each(app: TestClient) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    report = task(app, "보고서", now + timedelta(days=2, hours=1))
    task(app, "퀴즈", now + timedelta(hours=3))
    task(app, "지난 과제", now - timedelta(hours=2))
    task(app, "아주 오래전", now - timedelta(days=30))
    done = task(app, "끝난 일", now + timedelta(hours=5))
    app.patch(f"/api/v1/tasks/{done['id']}", json={"status": "done"})

    fresh = app.post("/api/v1/notifications/check").json()
    got = {n["title"] for n in fresh}
    assert any(t.startswith("D-") and t.endswith("보고서") for t in got)
    assert any(t.endswith("마감 · 퀴즈") for t in got)
    assert "마감 지남 · 지난 과제" in got
    assert not any("오래전" in t or "끝난 일" in t for t in got)
    assert app.post("/api/v1/notifications/check").json() == []  # once only

    app.patch(
        f"/api/v1/tasks/{report['id']}", json={"due_at": (now + timedelta(hours=20)).isoformat()}
    )
    [moved] = app.post("/api/v1/notifications/check").json()
    assert moved["title"].endswith("보고서") and "마감" in moved["title"]  # new date, new notice


def test_daily_roundups_come_after_the_digest_hour(app: TestClient, settings: Settings) -> None:
    course_id = course(app)
    app.post(f"/api/v1/channels/{course_id}/messages", json={"body": "나중에 정리할 메모"})
    task(app, "날짜 없는 일")
    later = datetime.now(UTC) + timedelta(days=5)
    early = later.astimezone(settings.zoneinfo).replace(hour=6).astimezone(UTC)
    assert [n.kind for n in run(app, early)] == []
    late = early + timedelta(hours=5)
    kinds = sorted(n.kind for n in run(app, late))
    assert kinds == ["inbox_stale", "undated"]
    assert run(app, late + timedelta(hours=1)) == []  # once a day


def test_notices_go_to_the_messenger_when_turned_on(app: TestClient) -> None:
    sent: list[tuple[str, str]] = []

    async def fake_send(config: Settings, target: str, text: str) -> str | None:
        sent.append((target, text))
        return None if "퀴즈" in text else "discord 전송 실패"

    app.app.state.notifier.send = fake_send  # type: ignore[attr-defined]
    now = datetime.now(UTC)
    task(app, "퀴즈", now + timedelta(hours=3))
    app.post("/api/v1/notifications/check")
    assert sent == []  # off by default

    saved = app.put(
        "/api/v1/settings/notify",
        json={
            "hermes_target": "discord:#general",
            "weekly_review_weekday": 6,
            "weekly_review_hour": 20,
        },
    )
    assert saved.json()["hermes_target"] == "discord:#general"
    task(app, "시험", now + timedelta(hours=4))
    task(app, "퀴즈 2", now + timedelta(hours=5))
    app.post("/api/v1/notifications/check")
    assert {t for t, _ in sent} == {"discord:#general"}
    items = {n["title"]: n for n in app.get("/api/v1/notifications").json()["items"]}
    quiz = next(n for t, n in items.items() if t.endswith("퀴즈 2"))
    assert quiz["sent_at"] is not None and quiz["send_error"] is None
    failed = next(n for t, n in items.items() if t.endswith("시험"))
    assert failed["send_error"] == "discord 전송 실패"


def test_reading_notices(app: TestClient) -> None:
    task(app, "퀴즈", datetime.now(UTC) + timedelta(hours=3))
    task(app, "시험", datetime.now(UTC) + timedelta(hours=4))
    app.post("/api/v1/notifications/check")
    listed = app.get("/api/v1/notifications").json()
    assert listed["unread"] == 2
    app.post(f"/api/v1/notifications/{listed['items'][0]['id']}/read")
    assert app.get("/api/v1/notifications").json()["unread"] == 1
    assert app.post("/api/v1/notifications/read-all").json()["unread"] == 0


# --- weekly review -----------------------------------------------------------------------------


def test_weekly_review_summarizes_the_week(app: TestClient) -> None:
    done = task(app, "캐시 실습")
    app.patch(f"/api/v1/tasks/{done['id']}", json={"status": "done"})
    task(app, "먼 훗날 할 일", status="backlog")
    review = app.post("/api/v1/review/weekly")
    assert review.status_code == 201
    text = review.json()["body"]
    assert text.startswith("# 주간 리뷰")
    assert "## 이번 주 끝낸 일 1개" in text and "**#컴퓨터구조** 1개: 캐시 실습" in text
    assert "그대로인 backlog 0개" in text  # it was just made
    today = next(c for c in app.get("/api/v1/channels").json()["channels"] if c["name"] == "today")
    assert review.json()["channel_id"] == today["id"]


def test_review_arrives_once_on_its_day(app: TestClient, settings: Settings) -> None:
    local = datetime.now(settings.zoneinfo)
    sunday = (local + timedelta(days=(6 - local.weekday()) % 7)).replace(hour=21, minute=0)
    fresh = run(app, sunday.astimezone(UTC))
    assert [n.kind for n in fresh if n.kind == "weekly_review"] == ["weekly_review"]
    again = run(app, (sunday + timedelta(hours=1)).astimezone(UTC))
    assert not [n for n in again if n.kind == "weekly_review"]
    today = next(c for c in app.get("/api/v1/channels").json()["channels"] if c["name"] == "today")
    items = app.get(f"/api/v1/channels/{today['id']}/messages").json()["items"]
    assert sum(m["body"].startswith("# 주간 리뷰") for m in items) == 1


def test_similar_inbox_notes_are_grouped(settings: Settings) -> None:
    import asyncio

    notes = ["캐시 지역성 정리", "캐시 적중률 공식", "헬스장 등록", "캐시 교체 정책", "PT 예약"]

    async def embed(texts: list[str]) -> list[list[float]]:
        # Like a real model: everything fairly alike, one topic a bit more so.
        return [[1.0, 0.35, 0.05] if "캐시" in t else [0.35, 1.0, 0.05] for t in texts]

    groups = asyncio.run(notify.idea_groups(notes, settings, embed))
    assert sorted(groups) == [
        ["캐시 지역성 정리", "캐시 적중률 공식", "캐시 교체 정책"],
        ["헬스장 등록", "PT 예약"],
    ]

    async def broken(texts: list[str]) -> list[list[float]]:
        raise ConnectionError("no ollama")

    assert asyncio.run(notify.idea_groups(notes, settings, broken)) == []


# --- numbers and briefing ----------------------------------------------------------------------


def test_analytics_and_briefing(app: TestClient) -> None:
    done = task(app, "캐시 실습")
    app.patch(f"/api/v1/tasks/{done['id']}", json={"status": "done"})
    task(app, "시험", datetime.now(UTC) + timedelta(hours=2))
    stats = app.get("/api/v1/analytics").json()
    assert stats["weekly_done"][-1]["count"] == 1 and len(stats["weekly_done"]) == 8
    assert stats["processing"][0]["channel"] == "컴퓨터구조"

    brief = app.get("/api/v1/briefing/today").json()
    assert [t["title"] for t in brief["due_tasks"]] == ["시험"]
    text = app.get("/api/v1/briefing/today", params={"format": "markdown"})
    assert text.headers["content-type"].startswith("text/markdown")
    assert "## 마감" in text.text and "시험" in text.text


# --- operations -------------------------------------------------------------------------------


def test_backups_keep_only_the_newest(tmp_path: Path) -> None:
    db = tmp_path / "argos.db"
    with sqlite3.connect(db) as conn:
        conn.execute("create table t (x)")
        conn.execute("insert into t values (1)")
    for n in range(4):
        ops.backup_db(db, tmp_path / "backups", 2, datetime(2026, 9, 25, 0, 0, n))
    kept = ops.list_backups(tmp_path / "backups")
    assert [p.name for p in kept] == ["argos-20260925-000002.db", "argos-20260925-000003.db"]
    with sqlite3.connect(kept[-1]) as conn:
        assert conn.execute("select x from t").fetchall() == [(1,)]


def test_backup_endpoint(app: TestClient, settings: Settings) -> None:
    out = app.post("/api/v1/ops/backup").json()
    assert out["backup_count"] == 1 and out["backup_last"] is not None
    assert app.put("/api/v1/ops/backup", json={"keep": 3}).json()["backup_keep"] == 3


def test_launch_agent_install_and_remove(
    app: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, ...]] = []
    plist = tmp_path / "LaunchAgents" / f"{ops.LABEL}.plist"

    class Done:
        def __init__(self, code: int) -> None:
            self.returncode, self.stdout, self.stderr = code, "", ""

    def fake(*args: str) -> Done:
        calls.append(args)
        return Done(0 if args[0] == "bootstrap" or plist.exists() else 113)

    monkeypatch.setattr(ops, "_launchctl", fake)
    monkeypatch.setattr(ops, "plist_path", lambda _label=ops.LABEL: plist)
    monkeypatch.setattr(ops.sys, "platform", "darwin")

    service = app.post("/api/v1/ops/service", json={}).json()["service"]
    assert service["installed"] and service["supported"]
    spec = plistlib.loads(plist.read_bytes())
    assert spec["Label"] == ops.LABEL and spec["ProgramArguments"][-2:] == [str(ops.REPO), "dev"]
    assert spec["RunAtLoad"] and "/usr/bin" in spec["EnvironmentVariables"]["PATH"]
    assert not any(c[0] == "bootstrap" for c in calls)  # starts at the next login only

    app.post("/api/v1/ops/service", json={"start_now": True})
    assert any(c[0] == "bootstrap" for c in calls)
    removed = app.delete("/api/v1/ops/service").json()["service"]
    assert not removed["installed"] and not plist.exists()
