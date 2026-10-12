import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fakes import FakeAgent, fake_agents
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from argos import notify, services
from argos.config import Settings
from argos.main import create_app
from argos.models import Channel, Event, LmsNotice

NOW = datetime.now(UTC).replace(microsecond=0)
LONG = "중간고사 범위는 3장부터 7장까지입니다. " * 20


async def course(session: AsyncSession) -> Channel:
    await services.seed_defaults(
        session, {"area": [{"name": "학업", "channel": [{"name": "자료구조"}]}]}
    )
    channel = await session.scalar(select(Channel).where(Channel.name == "자료구조"))
    assert channel is not None
    return channel


async def lms(session: AsyncSession, item_id: str, text: str) -> None:
    await services.import_lms(
        session,
        account_id="42",
        courses=[{"id": "7", "name": "알고리즘"}],
        items=[
            {
                "id": item_id,
                "course_id": "7",
                "kind": "announcement",
                "title": f"공지 {item_id}",
                "text": text,
                "url": "https://lms.example/1",
                "due_at": None,
                "file": None,
            }
        ],
    )


def seoul_today(settings: Settings, hour: int) -> datetime:
    local = NOW.astimezone(settings.zoneinfo)
    return local.replace(hour=hour, minute=0, second=0).astimezone(UTC)


async def test_digest_data_collects_today_lms_and_attention(
    session: AsyncSession, settings: Settings
) -> None:
    channel = await course(session)
    start = seoul_today(settings, 14)
    session.add_all(
        [
            Event(
                channel_id=channel.id,
                title="세미나",
                starts_at=start,
                ends_at=start + timedelta(hours=2),
            ),
            Event(
                channel_id=channel.id,
                title="면담",
                starts_at=start + timedelta(hours=1),
                ends_at=start + timedelta(hours=3),
            ),
        ]
    )
    await session.commit()
    await services.create_task(
        session,
        channel_id=channel.id,
        title="지난 과제",
        due_at=NOW - timedelta(hours=3),
        actor="u",
    )
    await lms(session, "a1", LONG)
    data = await notify.morning_digest_data(session, NOW + timedelta(minutes=1), settings)
    assert data["attention"]["overlaps"] == [["세미나", "면담"]]
    assert data["attention"]["overdue"] == 1
    assert [item["title"] for item in data["lms"]] == ["공지 a1"]
    assert data["lms"][0]["text"] == LONG[: notify.DIGEST_PREVIEW]
    assert data["lms"][0]["summary"] is None


async def test_digest_prefers_summaries_and_skips_old_lms(
    session: AsyncSession, settings: Settings
) -> None:
    await lms(session, "a1", LONG)
    notice = await session.scalar(select(LmsNotice))
    assert notice is not None
    notice.summary_state, notice.summary = "done", "- 3~7장"
    await session.commit()
    data = await notify.morning_digest_data(session, NOW + timedelta(minutes=1), settings)
    assert data["lms"][0]["summary"] == "- 3~7장" and data["lms"][0]["text"] is None
    later = await notify.morning_digest_data(session, NOW + timedelta(days=2), settings)
    assert later["lms"] == []


async def test_digest_caps_lms_items(session: AsyncSession, settings: Settings) -> None:
    for i in range(notify.DIGEST_LMS + 3):
        await lms(session, f"n{i:02d}", "내용 " * 10)
    data = await notify.morning_digest_data(session, NOW + timedelta(minutes=1), settings)
    assert len(data["lms"]) == notify.DIGEST_LMS


async def test_digest_prompt_carries_data_and_language(
    session: AsyncSession, settings: Settings
) -> None:
    data = await notify.morning_digest_data(session, NOW, settings)
    prompt = notify.digest_prompt(data, "ko")
    assert "지어내지 마라" in prompt
    assert json.loads(prompt.split("\n\n", 1)[1]) == data
    assert "Do not invent" in notify.digest_prompt(data, "en")


async def test_empty_day_digest_markdown(session: AsyncSession, settings: Settings) -> None:
    data = await notify.morning_digest_data(session, NOW, settings)
    text = notify.digest_markdown(data, "응답 시간이 초과됐어요", "ko")
    assert text.startswith("AI 요약을 만들지 못했어요 (응답 시간이 초과됐어요)")
    assert "## 새 LMS 소식" not in text and "## 챙길 것" not in text


# --- writing and sending the digest --------------------------------------------------

READY = "아침 요약이 도착했어요"


@pytest.fixture
def app(settings: Settings) -> Iterator[TestClient]:
    on = settings.model_copy(update={"morning_digest_enabled": True})
    with TestClient(create_app(on)) as client:
        yield client


def state(client: TestClient) -> Any:
    return client.app.state  # type: ignore[attr-defined]


def seoul(client: TestClient, hour: int, minute: int = 0) -> datetime:
    tz = state(client).settings.zoneinfo
    local = datetime.now(UTC).astimezone(tz)
    return local.replace(hour=hour, minute=minute, second=0, microsecond=0).astimezone(UTC)


def check(client: TestClient, now: datetime) -> None:
    notifier = state(client).notifier
    client.portal.call(notifier.run, now)  # type: ignore[union-attr]
    client.portal.call(notifier.digest.wait)  # type: ignore[union-attr]


def sends(client: TestClient, target: str | None = "discord") -> list[str]:
    sent: list[str] = []

    async def fake_send(config: Settings, to: str, text: str) -> str | None:
        sent.append(text)
        return None

    state(client).notifier.send = fake_send
    if target:
        settings = {"hermes_target": target, "weekly_review_weekday": 6, "weekly_review_hour": 20}
        client.put("/api/v1/settings/notify", json=settings)
    return sent


def digest_thread(client: TestClient) -> list[dict[str, Any]]:
    channels = client.get("/api/v1/channels").json()["channels"]
    today = next(c for c in channels if c["name"] == "today")
    items = client.get(f"/api/v1/channels/{today['id']}/messages").json()["items"]
    root = next(m for m in items if "아침 요약" in m["body"])
    thread = client.get(f"/api/v1/messages/{root['id']}/thread").json()
    return [thread["root"], *thread["replies"]]


def ready_count(client: TestClient) -> int:
    items = client.get("/api/v1/notifications").json()["items"]
    return [n["title"] for n in items].count(READY)


def test_agent_writes_the_digest_into_today_and_the_messenger(app: TestClient) -> None:
    hermes = FakeAgent("hermes", ["오늘은 ", "세미나가 있어요"])
    state(app).runner.adapter_factory = fake_agents(hermes=hermes)
    sent = sends(app)
    check(app, seoul(app, 7, 5))
    root, reply = digest_thread(app)
    assert reply["body"] == "오늘은 세미나가 있어요" and reply["author_type"] == "agent"
    assert root["sticky_agent_id"] is not None
    assert sent == ["오늘은 세미나가 있어요"]
    assert hermes.no_tools == [True]
    assert "지어내지 마라" in hermes.transcripts[0][0].text


def test_missing_agent_falls_back_to_the_list(app: TestClient) -> None:
    state(app).runner.adapter_factory = fake_agents()  # every agent unavailable
    sent = sends(app)
    check(app, seoul(app, 8))
    *_, fallback = digest_thread(app)
    assert fallback["author_type"] == "system"
    assert fallback["body"].startswith("AI 요약을 만들지 못했어요")
    assert sent == [fallback["body"]]


def test_unknown_agent_name_falls_back_to_the_list(app: TestClient) -> None:
    sent = sends(app)  # saving notice settings re-reads them, so set the agent after
    state(app).settings = state(app).settings.model_copy(
        update={"morning_digest_agent": "deleted-agent"}
    )
    check(app, seoul(app, 8))
    root, fallback = digest_thread(app)
    assert root["sticky_agent_id"] is None
    assert fallback["author_type"] == "system" and sent == [fallback["body"]]


def test_empty_answer_falls_back(app: TestClient) -> None:
    state(app).runner.adapter_factory = fake_agents(hermes=FakeAgent("hermes", []))
    sent = sends(app)
    check(app, seoul(app, 8))
    *_, last = digest_thread(app)
    assert last["author_type"] == "system" and sent == [last["body"]]


def test_digest_window(app: TestClient) -> None:
    state(app).runner.adapter_factory = fake_agents(hermes=FakeAgent("hermes", ["안녕"]))
    sends(app, target=None)
    for hour, minute in ((6, 59), (12, 0)):
        check(app, seoul(app, hour, minute))
    assert ready_count(app) == 0
    check(app, seoul(app, 11, 59))
    check(app, seoul(app, 11, 59))  # once a day
    assert ready_count(app) == 1


def test_digest_starts_once_while_running(app: TestClient) -> None:
    hold = asyncio.Event()
    slow = FakeAgent("hermes", ["느린 ", "답"], hold=hold)
    state(app).runner.adapter_factory = fake_agents(hermes=slow)
    sends(app, target=None)
    notifier = state(app).notifier
    app.portal.call(notifier.run, seoul(app, 7, 5))  # type: ignore[union-attr]
    app.portal.call(notifier.run, seoul(app, 7, 20))  # type: ignore[union-attr]
    app.portal.call(hold.set)  # type: ignore[union-attr]
    app.portal.call(notifier.digest.wait)  # type: ignore[union-attr]
    assert len(slow.transcripts) == 1 and ready_count(app) == 1


def test_off_means_no_digest(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        sends(client, target=None)
        check(client, seoul(client, 8))
        assert ready_count(client) == 0


def test_send_now_does_not_block_the_morning_digest(app: TestClient) -> None:
    state(app).runner.adapter_factory = fake_agents(hermes=FakeAgent("hermes", ["지금"]))
    sends(app, target=None)
    response = app.post("/api/v1/notify/digest")
    assert response.status_code == 200 and response.json()["message_id"]
    app.portal.call(state(app).notifier.digest.wait)  # type: ignore[union-attr]
    check(app, seoul(app, 8))
    assert ready_count(app) == 2


def started_run(client: TestClient) -> str:
    """Waits until the digest's agent run is going; returns its id."""
    runner = state(client).runner
    for _ in range(100):
        if runner.running():
            return runner.running()[0]
        client.portal.call(asyncio.sleep, 0.02)  # type: ignore[union-attr]
    raise AssertionError("the digest run never started")


def no_fallback(client: TestClient) -> bool:
    _root, *replies = digest_thread(client)
    return all(m["author_type"] != "system" for m in replies)


def test_shutdown_mid_digest_sends_nothing(app: TestClient) -> None:
    hold = asyncio.Event()
    state(app).runner.adapter_factory = fake_agents(hermes=FakeAgent("hermes", ["반"], hold=hold))
    sent = sends(app)
    notifier = state(app).notifier
    app.portal.call(notifier.run, seoul(app, 7, 5))  # type: ignore[union-attr]
    started_run(app)
    app.portal.call(notifier.digest.stop)  # type: ignore[union-attr]
    assert sent == [] and no_fallback(app)


def test_stop_button_cancels_the_digest(app: TestClient) -> None:
    hold = asyncio.Event()
    state(app).runner.adapter_factory = fake_agents(hermes=FakeAgent("hermes", ["반"], hold=hold))
    sent = sends(app)
    notifier = state(app).notifier
    app.portal.call(notifier.run, seoul(app, 7, 5))  # type: ignore[union-attr]
    run_id = started_run(app)
    assert app.post(f"/api/v1/runs/{run_id}/cancel").status_code == 202
    app.portal.call(notifier.digest.wait)  # type: ignore[union-attr]
    assert sent == [] and no_fallback(app)


NOTIFY_BASE = {"hermes_target": None, "weekly_review_weekday": 6, "weekly_review_hour": 20}
BRIEF: dict[str, Any] = {
    "name": "brief",
    "display_name": "브리핑",
    "avatar": "☀",
    "backend": "ollama",
    "model": "qwen3",
    "system_prompt": "짧게 쓴다.",
    "tools": [],
}


def test_deleting_the_digest_agent_clears_the_choice(app: TestClient) -> None:
    assert app.post("/api/v1/agents", json=BRIEF).status_code == 201
    chosen = NOTIFY_BASE | {"morning_digest_agent": "brief"}
    assert app.put("/api/v1/settings/notify", json=chosen).status_code == 200
    assert app.delete("/api/v1/agents/brief").status_code == 204
    refreshed = app.get("/api/v1/settings/notify").json()
    assert refreshed["morning_digest_agent"] == ""
    # The screen reloads these settings when agents change (invalidateFor "agent") and
    # saves the rest as before.
    again = NOTIFY_BASE | {"morning_digest_agent": "", "weekly_review_hour": 21}
    saved = app.put("/api/v1/settings/notify", json=again)
    assert saved.status_code == 200 and saved.json()["weekly_review_hour"] == 21


def test_renaming_the_digest_agent_follows_it(app: TestClient) -> None:
    assert app.post("/api/v1/agents", json=BRIEF).status_code == 201
    app.put("/api/v1/settings/notify", json=NOTIFY_BASE | {"morning_digest_agent": "brief"})
    assert app.patch("/api/v1/agents/brief", json={"name": "morning"}).status_code == 200
    assert app.get("/api/v1/settings/notify").json()["morning_digest_agent"] == "morning"


def test_unknown_agent_is_refused_before_anything_is_saved(app: TestClient) -> None:
    bad = NOTIFY_BASE | {"hermes_target": "discord", "morning_digest_agent": "nobody"}
    assert app.put("/api/v1/settings/notify", json=bad).status_code == 422

    async def stored() -> Any:  # the database, not the settings held in memory
        async with state(app).sessionmaker() as session:
            return await services.get_setting(session, "notify_hermes_target")

    assert app.portal.call(stored) is None  # type: ignore[union-attr]


async def test_backfilled_lms_cards_are_not_news(session: AsyncSession, settings: Settings) -> None:
    await lms(session, "a1", LONG)
    notice = await session.scalar(select(LmsNotice))
    assert notice is not None
    notice.kind = None  # a card made by 15a's backfill for an old message
    await session.commit()
    data = await notify.morning_digest_data(session, NOW + timedelta(minutes=1), settings)
    assert data["lms"] == []


async def test_empty_day_digest_list_follows_the_language(
    session: AsyncSession, settings: Settings
) -> None:
    data = await notify.morning_digest_data(session, NOW, settings)
    english = notify.digest_markdown(data, None, "en")
    assert english == "Nothing scheduled or due today."
    korean = notify.digest_markdown(data, None, "ko")
    assert korean == "오늘은 일정도 마감도 없어요."
