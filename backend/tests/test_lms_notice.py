from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from argos import services
from argos.models import LmsNotice, Message

LONG = "중간고사 범위는 3장부터 7장까지입니다. " * 20  # > 300 chars


def items(text: str = LONG) -> list[dict[str, Any]]:
    return [
        {
            "id": "11",
            "course_id": "7",
            "kind": "announcement",
            "title": "중간고사 *안내*",
            "text": text,
            "url": "https://lms.example/courses/7/announcements/11",
            "due_at": None,
            "file": None,
        }
    ]


async def run_import(
    session: AsyncSession, text: str = LONG, min_chars: int = 300
) -> dict[str, Any]:
    return await services.import_lms(
        session,
        account_id="42",
        courses=[{"id": "7", "name": "알고리즘"}],
        items=items(text),
        summary_min_chars=min_chars,
    )


async def notice(session: AsyncSession) -> LmsNotice:
    found = await session.scalar(select(LmsNotice))
    assert found is not None
    await session.refresh(found)
    return found


async def test_import_creates_pending_notice_for_long_text(session: AsyncSession) -> None:
    result = await run_import(session)
    row = await notice(session)
    assert result["summarize"] == [row.id]
    assert (row.kind, row.title, row.summary_state) == (
        "announcement",
        "중간고사 *안내*",
        "pending",
    )
    assert row.text == LONG and row.url.endswith("/11")


async def test_short_text_is_skipped(session: AsyncSession) -> None:
    result = await run_import(session, text="출석 처리되었습니다")
    assert result["summarize"] == []
    assert (await notice(session)).summary_state == "skipped"


async def test_import_without_summarizer_skips(session: AsyncSession) -> None:
    result = await run_import(session, min_chars=0)
    assert result["summarize"] == []
    assert (await notice(session)).summary_state == "skipped"


async def test_changed_text_goes_pending_again_and_clears_summary(session: AsyncSession) -> None:
    await run_import(session)
    row = await notice(session)
    assert await services.save_summary(session, row.id, LONG, "- 3~7장")
    result = await run_import(session, text=LONG + " 장소: 101호")
    row = await notice(session)
    assert result["summarize"] == [row.id]
    assert (row.summary_state, row.summary) == ("pending", None)


async def test_changed_text_while_pending_is_queued_again(session: AsyncSession) -> None:
    await run_import(session)
    row = await notice(session)
    result = await run_import(session, text=LONG + " 장소: 101호")
    assert result["summarize"] == [row.id]


async def test_unchanged_pending_notice_is_not_queued_twice(session: AsyncSession) -> None:
    await run_import(session)
    assert (await run_import(session))["summarize"] == []


async def test_failed_summary_retries_on_next_import(session: AsyncSession) -> None:
    await run_import(session)
    row = await notice(session)
    await services.fail_summary(session, row.id, LONG)
    assert (await notice(session)).summary_state == "failed"
    result = await run_import(session)  # same content
    assert result["summarize"] == [row.id]
    assert (await notice(session)).summary_state == "pending"


async def test_save_summary_updates_body_and_drops_stale_text(session: AsyncSession) -> None:
    await run_import(session)
    row = await notice(session)
    assert not await services.save_summary(session, row.id, "다른 원문", "- 옛 요약")
    assert await services.save_summary(session, row.id, LONG, "- 3~7장")
    row = await notice(session)
    assert (row.summary_state, row.summary) == ("done", "- 3~7장")
    message = await session.get(Message, row.message_id)
    assert message is not None
    await session.refresh(message)
    assert message.body == (
        "중간고사 \\*안내\\*\n\n- 3~7장\n\nhttps://lms.example/courses/7/announcements/11"
    )


async def test_pending_summaries_lists_waiting_notices(session: AsyncSession) -> None:
    await run_import(session)
    row = await notice(session)
    assert await services.pending_summaries(session) == [row.id]


def test_split_lms_body_unescapes_markdown() -> None:
    body = services.lms_body("공지 *1*", "본문 [링크]\n\n둘째 문단", "https://lms.example/1")
    assert services.split_lms_body(body) == (
        "공지 *1*",
        "본문 [링크]\n\n둘째 문단",
        "https://lms.example/1",
    )
    assert services.split_lms_body("사용자가 쓴 메시지") is None


async def test_backfill_creates_skipped_notices_for_old_messages(session: AsyncSession) -> None:
    await run_import(session)
    old = await notice(session)
    await session.delete(old)  # as if imported before Phase 15a
    await session.commit()
    assert await services.backfill_lms_notices(session) == 1
    row = await notice(session)
    assert (row.kind, row.summary_state, row.title) == (None, "skipped", "중간고사 *안내*")
    assert await services.backfill_lms_notices(session) == 0


async def test_done_summary_of_other_text_is_redone(session: AsyncSession) -> None:
    """A summary saved for text the notice no longer has (a lost race) heals on the next sync."""
    await run_import(session)
    row = await notice(session)
    row.summary_state, row.summary, row.summary_hash = "done", "- 옛 요약", "0" * 64
    await session.commit()
    result = await run_import(session)  # same text as stored
    row = await notice(session)
    assert result["summarize"] == [row.id]
    assert (row.summary_state, row.summary) == ("pending", None)


async def test_summary_min_chars_setting(session: AsyncSession) -> None:
    assert await services.lms_summary_min_chars(session, 300) == 300
    await services.set_lms_summary_min_chars(session, 0, "user")
    assert await services.lms_summary_min_chars(session, 300) == 0
