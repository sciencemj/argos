from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from argos import services
from argos.models import ActivityLog, Channel, Event, InboxStatus, Task, TaskStatus

SEOUL = ZoneInfo("Asia/Seoul")


async def make_channel(session: AsyncSession, name: str = "컴퓨터구조") -> Channel:
    await services.seed_defaults(session, {"area": [{"name": "학업", "channel": [{"name": name}]}]})
    channel = await session.scalar(select(Channel).where(Channel.name == name))
    assert channel is not None
    return channel


async def activity(session: AsyncSession, object_id: str) -> list[ActivityLog]:
    query = select(ActivityLog).where(ActivityLog.object_id == object_id)
    return list((await session.scalars(query.order_by(ActivityLog.created_at))).all())


async def column_titles(session: AsyncSession, channel_id: str, status: TaskStatus) -> list[str]:
    tasks = await services.list_tasks(session, channel_id=channel_id, status=status)
    return [t.title for t in tasks]


# --- seed ---------------------------------------------------------------------


async def test_seed_is_idempotent(session: AsyncSession) -> None:
    def config(vault_path: str) -> dict[str, Any]:
        channel = {"name": "운영체제", "vault_path": vault_path}
        return {"area": [{"name": "학업", "channel": [channel]}]}

    await services.seed_defaults(session, config("a/"))
    await services.seed_defaults(session, config("b/"))

    channels = await services.list_channels(session)
    assert [c.name for c in channels] == ["today", "inbox", "운영체제"]
    assert channels[2].vault_path == "b/"


# --- tasks --------------------------------------------------------------------


async def test_create_task_logs_activity_with_actor(session: AsyncSession) -> None:
    channel = await make_channel(session)
    task = await services.create_task(
        session, channel_id=channel.id, title="과제2", actor="agent:claude"
    )

    [log] = await activity(session, task.id)
    assert (log.action, log.actor) == ("created", "agent:claude")
    assert log.after_json is not None and log.after_json["title"] == "과제2"


async def test_create_task_in_unknown_channel_fails(session: AsyncSession) -> None:
    with pytest.raises(services.NotFoundError):
        await services.create_task(session, channel_id="nope", title="x", actor="user")


async def test_status_change_is_logged_and_appends_to_new_column(session: AsyncSession) -> None:
    channel = await make_channel(session)
    done = await services.create_task(
        session, channel_id=channel.id, title="old", status=TaskStatus.DONE, actor="user"
    )
    task = await services.create_task(session, channel_id=channel.id, title="new", actor="user")

    await services.update_task(session, task.id, {"status": TaskStatus.DONE}, "user")

    assert task.position > done.position
    logs = await activity(session, task.id)
    assert logs[-1].action == "updated"
    assert logs[-1].before_json is not None and logs[-1].before_json["status"] == "todo"
    assert logs[-1].after_json is not None and logs[-1].after_json["status"] == "done"


async def test_update_without_real_change_logs_nothing(session: AsyncSession) -> None:
    channel = await make_channel(session)
    task = await services.create_task(session, channel_id=channel.id, title="same", actor="user")
    await services.update_task(session, task.id, {"title": "same"}, "user")
    assert [log.action for log in await activity(session, task.id)] == ["created"]


async def test_move_task_between_and_across_columns(session: AsyncSession) -> None:
    channel = await make_channel(session)
    a, b, c = [
        await services.create_task(session, channel_id=channel.id, title=t, actor="user")
        for t in "abc"
    ]

    await services.move_task(session, c.id, status=TaskStatus.TODO, after_id=a.id, actor="user")
    assert await column_titles(session, channel.id, TaskStatus.TODO) == ["a", "c", "b"]

    await services.move_task(session, b.id, status=TaskStatus.TODO, before_id=a.id, actor="user")
    assert await column_titles(session, channel.id, TaskStatus.TODO) == ["b", "a", "c"]

    await services.move_task(session, a.id, status=TaskStatus.IN_PROGRESS, actor="user")
    assert await column_titles(session, channel.id, TaskStatus.TODO) == ["b", "c"]
    assert await column_titles(session, channel.id, TaskStatus.IN_PROGRESS) == ["a"]
    assert (await activity(session, a.id))[-1].action == "moved"


async def test_move_renumbers_when_gap_is_exhausted(session: AsyncSession) -> None:
    channel = await make_channel(session)
    a, b, c = [
        await services.create_task(session, channel_id=channel.id, title=t, actor="user")
        for t in "abc"
    ]
    a.position, b.position = 1.0, 1.0 + 1e-9
    await session.commit()

    await services.move_task(session, c.id, status=TaskStatus.TODO, after_id=a.id, actor="user")

    assert await column_titles(session, channel.id, TaskStatus.TODO) == ["a", "c", "b"]
    assert b.position - a.position > 1.0


async def test_move_with_anchor_from_other_column_fails(session: AsyncSession) -> None:
    channel = await make_channel(session)
    a = await services.create_task(session, channel_id=channel.id, title="a", actor="user")
    b = await services.create_task(session, channel_id=channel.id, title="b", actor="user")
    with pytest.raises(services.InvalidError):
        await services.move_task(session, a.id, status=TaskStatus.DONE, after_id=b.id, actor="user")


async def test_delete_task_keeps_snapshot_in_log(session: AsyncSession) -> None:
    channel = await make_channel(session)
    task = await services.create_task(session, channel_id=channel.id, title="gone", actor="user")
    await services.delete_task(session, task.id, "user")

    assert await session.get(Task, task.id) is None
    log = (await activity(session, task.id))[-1]
    assert log.action == "deleted"
    assert log.before_json is not None and log.before_json["title"] == "gone"


# --- events -------------------------------------------------------------------


async def test_all_day_event_defaults_to_single_day(session: AsyncSession) -> None:
    channel = await make_channel(session)
    event = await services.create_event(
        session, channel_id=channel.id, title="시험", start_date=date(2026, 10, 20), actor="user"
    )
    assert event.all_day
    assert event.end_date == date(2026, 10, 21)


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"starts_at": datetime(2026, 1, 1, tzinfo=UTC), "start_date": date(2026, 1, 1)},
        {
            "starts_at": datetime(2026, 1, 2, tzinfo=UTC),
            "ends_at": datetime(2026, 1, 1, tzinfo=UTC),
        },
        {"start_date": date(2026, 1, 2), "end_date": date(2026, 1, 2)},
    ],
)
async def test_invalid_event_shapes_are_rejected(
    session: AsyncSession, fields: dict[str, Any]
) -> None:
    channel = await make_channel(session)
    with pytest.raises(services.InvalidError):
        await services.create_event(
            session, channel_id=channel.id, title="bad", actor="user", **fields
        )


async def test_invalid_event_update_leaves_event_unchanged(session: AsyncSession) -> None:
    channel = await make_channel(session)
    start = datetime(2026, 9, 25, 1, tzinfo=UTC)
    event = await services.create_event(
        session, channel_id=channel.id, title="회의", starts_at=start, actor="user"
    )
    with pytest.raises(services.InvalidError):
        await services.update_event(session, event.id, {"start_date": date(2026, 9, 25)}, "user")

    stored = await session.get(Event, event.id)
    assert stored is not None
    assert (stored.starts_at, stored.start_date) == (start, None)
    assert [log.action for log in await activity(session, event.id)] == ["created"]


async def test_list_events_uses_local_day_for_all_day_and_timed(session: AsyncSession) -> None:
    channel = await make_channel(session)
    # 00:30 KST on the 25th is still the 24th in UTC.
    early = await services.create_event(
        session,
        channel_id=channel.id,
        title="early",
        starts_at=datetime(2026, 9, 25, 0, 30, tzinfo=SEOUL),
        ends_at=datetime(2026, 9, 25, 1, 30, tzinfo=SEOUL),
        actor="user",
    )
    all_day = await services.create_event(
        session, channel_id=channel.id, title="all", start_date=date(2026, 9, 25), actor="user"
    )

    day25 = datetime(2026, 9, 25, tzinfo=SEOUL)
    on_25 = await services.list_events(session, start=day25, end=day25 + timedelta(1), tz=SEOUL)
    on_24 = await services.list_events(session, start=day25 - timedelta(1), end=day25, tz=SEOUL)

    assert {e.id for e in on_25} == {early.id, all_day.id}
    assert on_24 == []


# --- inbox & today ------------------------------------------------------------


async def test_inbox_cursor_pages_newest_first(session: AsyncSession) -> None:
    for i in range(5):
        await services.create_inbox_item(session, raw_text=f"idea {i}", captured_via="t", actor="u")

    page1, cursor = await services.list_inbox(session, limit=2)
    page2, cursor2 = await services.list_inbox(session, limit=2, cursor=cursor)
    page3, cursor3 = await services.list_inbox(session, limit=2, cursor=cursor2)

    texts = [i.raw_text for i in [*page1, *page2, *page3]]
    assert texts == [f"idea {i}" for i in reversed(range(5))]
    assert cursor3 is None


async def test_malformed_cursor_is_invalid(session: AsyncSession) -> None:
    with pytest.raises(services.InvalidError):
        await services.list_inbox(session, cursor="garbage")


async def test_today_collects_events_due_tasks_and_open_inbox(session: AsyncSession) -> None:
    channel = await make_channel(session)
    now = datetime(2026, 9, 25, 9, tzinfo=SEOUL)
    make = services.create_task
    overdue = await make(
        session, channel_id=channel.id, title="late", due_at=now - timedelta(2), actor="u"
    )
    soon = await make(
        session, channel_id=channel.id, title="soon", due_at=now + timedelta(3), actor="u"
    )
    await make(session, channel_id=channel.id, title="far", due_at=now + timedelta(5), actor="u")
    await make(
        session,
        channel_id=channel.id,
        title="finished",
        due_at=now,
        status=TaskStatus.DONE,
        actor="u",
    )
    await make(session, channel_id=channel.id, title="undated", actor="u")
    event = await services.create_event(
        session, channel_id=channel.id, title="수업", starts_at=now, actor="u"
    )
    await services.create_inbox_item(session, raw_text="a", captured_via="t", actor="u")
    handled = await services.create_inbox_item(session, raw_text="b", captured_via="t", actor="u")
    await services.update_inbox_item(session, handled.id, {"status": InboxStatus.ACCEPTED}, "u")

    today = await services.get_today(session, now=now, tz=SEOUL, due_soon_days=3)

    assert [e.id for e in today["events"]] == [event.id]
    assert [t.id for t in today["due_tasks"]] == [overdue.id, soon.id]
    assert today["inbox_count"] == 1


async def test_every_write_is_logged(session: AsyncSession) -> None:
    channel = await make_channel(session)
    task = await services.create_task(session, channel_id=channel.id, title="t", actor="u")
    await services.update_task(session, task.id, {"title": "t2"}, "u")
    await services.move_task(session, task.id, status=TaskStatus.REVIEW, actor="u")
    await services.delete_task(session, task.id, "u")
    count = await session.scalar(select(func.count()).where(ActivityLog.object_id == task.id))
    assert count == 4
