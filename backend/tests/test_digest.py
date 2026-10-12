import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from argos import notify, services
from argos.config import Settings
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
