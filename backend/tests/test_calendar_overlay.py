"""Calendar events Argos may not write (v0.2.2): their channel is Argos's own overlay
(kept by calendar UID across re-syncs), agents see where each event lives and whether
it is editable, a change Argos cannot make fails before any approval, and create_event
refuses an exact duplicate unless forced."""

from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from argos import argos_tools, services
from argos.config import Settings
from argos.db import make_engine, make_sessionmaker
from argos.harness import ToolContext
from argos.models import Channel, ChannelKind, Event, EventChannel, SourceLink

DAY = date(2026, 11, 22)


@pytest.fixture
async def maker(settings: Settings) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = make_engine(settings.db_url)
    yield make_sessionmaker(engine)
    await engine.dispose()


async def setup(maker: async_sessionmaker[AsyncSession]) -> dict[str, Any]:
    async with maker() as s:
        daily = Channel(name="일상", kind=ChannelKind.PERSONAL)
        course = Channel(name="비즈니스애널리틱스II", kind=ChannelKind.COURSE)
        s.add_all([daily, course])
        await s.flush()
        work = Event(
            channel_id=daily.id,
            title="BA 데이터 전처리 및 2 (b)",
            start_date=DAY,
            end_date=date(2026, 11, 23),
        )
        s.add(work)
        await s.flush()
        s.add(
            SourceLink(
                object_type="event",
                object_id=work.id,
                source="icloud",
                external_id="/cal/work/1.ics",
                container="/cal/work/",
                container_name="직장",
                uid="uid-1",
                read_only=True,
            )
        )
        await s.commit()
        return {"daily": daily.id, "course": course.id, "event": work.id}


def ctx(maker: async_sessionmaker[AsyncSession], tmp_path: Path) -> ToolContext:
    return ToolContext(workspace=tmp_path, agent="claude", sessionmaker=maker)


async def test_schedule_says_where_events_live(
    maker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    ids = await setup(maker)
    [event] = await argos_tools.get_schedule(ctx(maker, tmp_path), "2026-11-22", "2026-11-23")
    assert (event["id"], event["source"], event["calendar"], event["editable"]) == (
        ids["event"],
        "calendar",
        "직장",
        False,
    )


async def test_channel_change_works_and_edits_fail_with_the_hint(
    maker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    ids = await setup(maker)
    tools = ctx(maker, tmp_path)
    moved = await argos_tools.update_event(tools, ids["event"], channel="비즈니스애널리틱스II")
    assert (moved["channel"], moved["editable"]) == ("비즈니스애널리틱스II", False)
    async with maker() as s:  # remembered by calendar UID
        row = await s.scalar(select(EventChannel).where(EventChannel.uid == "uid-1"))
        assert row is not None and row.channel_id == ids["course"]
    with pytest.raises(services.ConflictError, match="'직장' 캘린더 일정이라") as edit:
        await argos_tools.update_event(tools, ids["event"], title="고침")
    assert "update_event의 channel" in str(edit.value)
    with pytest.raises(services.ConflictError, match="수정·삭제할 수 없어요"):
        await argos_tools.delete_event(tools, ids["event"])
    # The harness asks before running delete: its precheck refuses first.
    precheck = argos_tools.tool_def(argos_tools.ARGOS_TOOLS["delete_event"]).precheck
    assert precheck is not None
    with pytest.raises(services.ConflictError):
        await precheck({"event_id": ids["event"]}, tools)
    channel_only = argos_tools.tool_def(argos_tools.ARGOS_TOOLS["update_event"]).precheck
    assert channel_only is not None
    await channel_only({"event_id": ids["event"], "channel": "일상"}, tools)  # allowed


async def test_create_event_refuses_a_duplicate_unless_forced(
    maker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    ids = await setup(maker)
    tools = ctx(maker, tmp_path)
    with pytest.raises(services.ConflictError, match=ids["event"]) as refused:
        await argos_tools.create_event(
            tools, "BA 데이터  전처리 및 2 (b)", "비즈니스애널리틱스II", "2026-11-22"
        )
    assert "'직장' 캘린더" in str(refused.value) and "force=true" in str(refused.value)
    copy = await argos_tools.create_event(
        tools, "BA 데이터 전처리 및 2 (b)", "비즈니스애널리틱스II", "2026-11-22", force=True
    )
    # Listed once: the copy folds into the calendar's original.
    [event] = await argos_tools.get_schedule(tools, "2026-11-22", "2026-11-23")
    assert (event["id"], event["duplicates"]) == (ids["event"], [copy["id"]])
    # A different day or title is a different event.
    other = await argos_tools.create_event(tools, "BA 데이터 전처리 및 2 (b)", "일상", "2026-11-23")
    assert other["source"] == "argos" and other["editable"] is True


async def test_synced_series_times_and_delete_fail_before_approval(
    maker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    ids = await setup(maker)
    async with maker() as s:  # a series on a calendar Argos may write
        series = Event(
            channel_id=ids["daily"],
            title="스터디",
            start_date=DAY,
            rrule="FREQ=WEEKLY",
        )
        s.add(series)
        await s.flush()
        s.add(
            SourceLink(
                object_type="event", object_id=series.id, source="icloud",
                external_id="/cal/argos/s.ics", container="/cal/argos/",
                container_name="Argos", uid="uid-s", read_only=False,
            )
        )  # fmt: skip
        await s.commit()
        series_id = series.id
    tools = ctx(maker, tmp_path)
    update = argos_tools.tool_def(argos_tools.ARGOS_TOOLS["update_event"]).precheck
    delete = argos_tools.tool_def(argos_tools.ARGOS_TOOLS["delete_event"]).precheck
    assert update is not None and delete is not None
    with pytest.raises(services.ConflictError, match="반복 일정은 캘린더 앱에서 고쳐 주세요"):
        await update({"event_id": series_id, "start": "2026-11-23"}, tools)
    with pytest.raises(services.ConflictError, match="반복 일정은"):
        await delete({"event_id": series_id}, tools)
    # Its title and its channel may still change from Argos.
    await update({"event_id": series_id, "title": "스터디 (새)"}, tools)
    moved = await argos_tools.update_event(tools, series_id, channel="비즈니스애널리틱스II")
    assert moved["channel"] == "비즈니스애널리틱스II"
