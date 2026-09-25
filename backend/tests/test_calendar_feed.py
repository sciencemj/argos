# pyright: reportUnknownMemberType=false
"""ICS feed for Apple Calendar subscriptions (PLAN Phase 7a)."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from icalendar import Calendar

from argos.config import Settings
from argos.main import create_app


def course(client: TestClient) -> str:
    channels = client.get("/api/v1/channels").json()["channels"]
    return next(c["id"] for c in channels if c["name"] == "컴퓨터구조")


def feed_path(client: TestClient) -> str:
    return client.get("/api/v1/settings/calendar").json()["path"]


def fetch(client: TestClient) -> dict[str, Any]:
    response = client.get(feed_path(client))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/calendar")
    cal = Calendar.from_ical(response.content)
    assert cal["X-WR-CALNAME"] == "Argos"
    return {str(e["UID"]): e for e in cal.walk("VEVENT")}


def test_feed_needs_the_token(client: TestClient) -> None:
    assert client.get("/api/v1/calendar/feed.ics").status_code == 404
    assert client.get("/api/v1/calendar/feed.ics?token=guess").status_code == 404
    assert client.get(feed_path(client)).status_code == 200
    assert feed_path(client) == feed_path(client)  # stable until rotated


def test_rotating_the_token_retires_the_old_url(client: TestClient) -> None:
    old = feed_path(client)
    new = client.post("/api/v1/settings/calendar/rotate").json()["path"]
    assert new != old
    assert client.get(old).status_code == 404
    assert client.get(new).status_code == 200


def test_token_stays_out_of_the_activity_log(client: TestClient) -> None:
    token = client.get("/api/v1/settings/calendar").json()["token"]
    client.post("/api/v1/settings/calendar/rotate")
    # Every write path that records activity is visible through the feed endpoints;
    # the token must not be one of them.
    for kind in ("tasks", "events", "inbox"):
        assert token not in client.get(f"/api/v1/{kind}").text


def test_feed_exports_events_and_open_deadlines(client: TestClient) -> None:
    channel = course(client)
    now = datetime.now(UTC).replace(microsecond=0)
    lab = client.post(
        "/api/v1/events",
        json={
            "channel_id": channel,
            "title": "실습 3",
            "starts_at": (now + timedelta(days=1)).isoformat(),
            "ends_at": (now + timedelta(days=1, hours=2)).isoformat(),
            "location": "공학관 401",
        },
    ).json()
    exam = client.post(
        "/api/v1/events",
        json={
            "channel_id": channel,
            "title": "중간고사 기간",
            "start_date": "2026-10-20",
            "end_date": "2026-10-25",
        },
    ).json()
    weekly = client.post(
        "/api/v1/events",
        json={
            "channel_id": channel,
            "title": "주간 강의",
            "starts_at": (now - timedelta(days=200)).isoformat(),
            "ends_at": (now - timedelta(days=200, hours=-1)).isoformat(),
            "rrule": "FREQ=WEEKLY;BYDAY=TU",
        },
    ).json()
    stale = client.post(
        "/api/v1/events",
        json={
            "channel_id": channel,
            "title": "작년 일정",
            "starts_at": (now - timedelta(days=200)).isoformat(),
            "ends_at": (now - timedelta(days=200, hours=-1)).isoformat(),
        },
    ).json()
    due = now + timedelta(days=3)
    report = client.post(
        "/api/v1/tasks",
        json={"channel_id": channel, "title": "보고서 제출", "due_at": due.isoformat()},
    ).json()
    finished = client.post(
        "/api/v1/tasks",
        json={"channel_id": channel, "title": "끝난 과제", "due_at": due.isoformat()},
    ).json()
    client.patch(f"/api/v1/tasks/{finished['id']}", json={"status": "done"})
    client.post("/api/v1/tasks", json={"channel_id": channel, "title": "날짜 없는 일"})

    items = fetch(client)
    assert set(items) == {
        f"event-{lab['id']}@argos",
        f"event-{exam['id']}@argos",
        f"event-{weekly['id']}@argos",
        f"task-{report['id']}@argos",
    }
    assert f"event-{stale['id']}@argos" not in items

    timed = items[f"event-{lab['id']}@argos"]
    assert timed["DTSTART"].dt == now + timedelta(days=1)
    assert timed["LOCATION"] == "공학관 401"
    assert timed["CATEGORIES"].cats == ["컴퓨터구조"]

    all_day = items[f"event-{exam['id']}@argos"]
    assert (all_day["DTSTART"].dt, all_day["DTEND"].dt) == (date(2026, 10, 20), date(2026, 10, 25))

    assert items[f"event-{weekly['id']}@argos"]["RRULE"]["FREQ"] == ["WEEKLY"]

    deadline = items[f"task-{report['id']}@argos"]
    assert deadline["SUMMARY"] == "마감 · 보고서 제출"
    assert deadline["DTEND"].dt == due  # ends at the deadline, never runs past it
    assert deadline["DTSTART"].dt == due - timedelta(minutes=30)
    assert deadline["TRANSP"] == "TRANSPARENT"


def test_feed_is_stable_across_fetches(client: TestClient) -> None:
    """Calendar apps match items by UID: a second fetch must not look like new events."""
    client.post(
        "/api/v1/events",
        json={"channel_id": course(client), "title": "세미나", "start_date": "2026-11-02"},
    )
    first, second = fetch(client), fetch(client)
    assert set(first) == set(second)
    [item] = first.values()
    assert item["DTEND"].dt == date(2026, 11, 3)  # missing end → the next day (exclusive)


def test_public_url_gives_the_full_feed_address(settings: Settings) -> None:
    config = settings.model_copy(update={"public_url": "https://argos.example.ts.net/"})
    with TestClient(create_app(config)) as client:
        feed = client.get("/api/v1/settings/calendar").json()
        assert feed["url"] == f"https://argos.example.ts.net{feed['path']}"


def test_without_public_url_the_client_adds_its_origin(client: TestClient) -> None:
    assert client.get("/api/v1/settings/calendar").json()["url"] is None
