# icalendar types its property values as Unknown; the rest of this module is strict.
# pyright: reportUnknownMemberType=false
"""ICS feed of Argos events and deadlines for calendar apps to subscribe to (PLAN 7a).

One-way: Apple Calendar polls the feed; nothing flows back. Timed values are written
in UTC, so no VTIMEZONE block is needed; all-day events stay plain dates."""

from collections.abc import Sequence
from datetime import datetime, timedelta

from icalendar import Calendar, vRecur
from icalendar import Event as VEvent
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from argos.models import Channel, Event, SourceLink, Task, TaskStatus

HISTORY = timedelta(days=90)  # past events kept in the feed
DEADLINE_LENGTH = timedelta(minutes=30)  # a deadline shows as the half hour before it
REFRESH = timedelta(minutes=10)  # how often subscribers should poll


async def build_feed(session: AsyncSession, now: datetime, tz_name: str) -> bytes:
    since = now - HISTORY
    events = (
        await session.scalars(
            select(Event).where(
                # Events synced with Apple calendars are already there (Phase 7b).
                Event.id.not_in(select(SourceLink.object_id)),
                or_(
                    Event.rrule.is_not(None),  # a series may have started long ago
                    Event.starts_at >= since,
                    Event.ends_at >= since,
                    Event.end_date >= since.date(),
                ),
            )
        )
    ).all()
    tasks = (
        await session.scalars(
            select(Task).where(
                Task.due_at.is_not(None), Task.due_at >= since, Task.status != TaskStatus.DONE
            )
        )
    ).all()
    channels = {c.id: c.name for c in (await session.scalars(select(Channel))).all()}
    return render(events, tasks, channels, now, tz_name)


def render(
    events: Sequence[Event],
    tasks: Sequence[Task],
    channels: dict[str, str],
    now: datetime,
    tz_name: str,
) -> bytes:
    cal = Calendar()
    cal.add("prodid", "-//Argos//Argos//KO")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("x-wr-calname", "Argos")
    cal.add("x-wr-timezone", tz_name)
    cal.add("refresh-interval", REFRESH, parameters={"VALUE": "DURATION"})
    cal.add("x-published-ttl", "PT10M")  # Apple Calendar reads this older name

    for event in events:
        item = _base(f"event-{event.id}", event.title, event.updated_at, now)
        if event.start_date is not None:
            item.add("dtstart", event.start_date)
            item.add("dtend", event.end_date or event.start_date + timedelta(days=1))
        elif event.starts_at is not None:
            item.add("dtstart", event.starts_at)
            if event.ends_at is not None:
                item.add("dtend", event.ends_at)
        else:
            continue
        if event.location:
            item.add("location", event.location)
        if event.rrule:
            item.add("rrule", vRecur.from_ical(event.rrule.removeprefix("RRULE:")))
        _channel(item, channels.get(event.channel_id))
        cal.add_component(item)

    for task in tasks:
        assert task.due_at is not None
        item = _base(f"task-{task.id}", f"마감 · {task.title}", task.updated_at, now)
        item.add("dtstart", task.due_at - DEADLINE_LENGTH)
        item.add("dtend", task.due_at)
        item.add("transp", "TRANSPARENT")  # a deadline does not make the user busy
        if task.description:
            item.add("description", task.description)
        _channel(item, channels.get(task.channel_id))
        cal.add_component(item)

    return cal.to_ical()


def _base(uid: str, summary: str, modified: datetime, now: datetime) -> VEvent:
    item = VEvent()
    item.add("uid", f"{uid}@argos")  # stable, so calendar apps update instead of duplicating
    item.add("summary", summary)
    item.add("dtstamp", now)
    item.add("last-modified", modified)
    return item


def _channel(item: VEvent, name: str | None) -> None:
    if name:
        item.add("categories", [name])
