"""Domain services. Every write in the app goes through here (PLAN §4) and leaves an
activity_log row in the same transaction."""

import tomllib
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from argos.hub import hub
from argos.models import (
    ActivityLog,
    AppSetting,
    Area,
    AuthorType,
    Channel,
    ChannelKind,
    Event,
    InboxItem,
    InboxStatus,
    Message,
    Record,
    Routine,
    RoutineCheck,
    Task,
    TaskStatus,
)

POSITION_STEP = 1024.0
MIN_POSITION_GAP = 1e-6
SYSTEM_CHANNELS = ("today", "inbox")
PERSONAL_CHANNEL = "일상"


class NotFoundError(Exception):
    def __init__(self, object_type: str, object_id: str) -> None:
        super().__init__(f"{object_type} {object_id} not found")
        self.object_type = object_type


class InvalidError(Exception):
    pass


class ConflictError(Exception):
    pass


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def snapshot(obj: Record) -> dict[str, Any]:
    return {c.key: _jsonable(getattr(obj, c.key)) for c in obj.__table__.columns}


def _log(
    session: AsyncSession,
    obj: Record,
    action: str,
    actor: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    session.add(
        ActivityLog(
            object_type=obj.__tablename__,
            object_id=obj.id,
            action=action,
            before_json=before,
            after_json=after,
            actor=actor,
        )
    )


async def _get[T: Record](session: AsyncSession, model: type[T], object_id: str) -> T:
    obj = await session.get(model, object_id)
    if obj is None:
        raise NotFoundError(model.__tablename__, object_id)
    return obj


async def _create[T: Record](session: AsyncSession, obj: T, actor: str) -> T:
    session.add(obj)
    await session.flush()
    after = snapshot(obj)
    _log(session, obj, "created", actor, after=after)
    await session.commit()
    await _publish("object.created", obj, after)
    return obj


async def _update[T: Record](
    session: AsyncSession,
    obj: T,
    changes: dict[str, Any],
    actor: str,
    action: str = "updated",
) -> T:
    before = snapshot(obj)
    for key, value in changes.items():
        setattr(obj, key, value)
    await session.flush()
    after = snapshot(obj)
    diff = {k for k in after if after[k] != before[k] and k != "updated_at"}
    if diff:
        _log(
            session,
            obj,
            action,
            actor,
            before={k: before[k] for k in diff},
            after={k: after[k] for k in diff},
        )
    await session.commit()
    if diff:
        await _publish("object.updated", obj, after)
    return obj


async def _delete(session: AsyncSession, obj: Record, actor: str) -> None:
    before = snapshot(obj)
    _log(session, obj, "deleted", actor, before=before)
    await session.delete(obj)
    await session.commit()
    await _publish("object.deleted", obj, before)


async def _publish(type_: str, obj: Record, fields: dict[str, Any]) -> None:
    """Sent after commit so clients never see a write that was rolled back. New chat
    messages use their own event name (PLAN §7.2)."""
    if isinstance(obj, Message) and type_ == "object.created":
        type_ = "message.created"
    await hub.publish(type_, {"object_type": obj.__tablename__, "id": obj.id, "object": fields})


# --- app settings -----------------------------------------------------------------


async def get_settings_overrides(session: AsyncSession) -> dict[str, Any]:
    rows = (await session.scalars(select(AppSetting))).all()
    return {row.key: row.value for row in rows}


async def set_setting(session: AsyncSession, key: str, value: Any, actor: str) -> AppSetting:
    row = await session.scalar(select(AppSetting).where(AppSetting.key == key))
    if row is None:
        return await _create(session, AppSetting(key=key, value=value), actor)
    return await _update(session, row, {"value": value}, actor)


# --- activity -----------------------------------------------------------------


async def list_activity(session: AsyncSession, object_id: str) -> Sequence[ActivityLog]:
    query = (
        select(ActivityLog)
        .where(ActivityLog.object_id == object_id)
        .order_by(ActivityLog.created_at, ActivityLog.id)
    )
    return (await session.scalars(query)).all()


# --- channels -----------------------------------------------------------------


async def list_areas(session: AsyncSession) -> Sequence[Area]:
    return (await session.scalars(select(Area).order_by(Area.sort_order, Area.name))).all()


async def list_channels(session: AsyncSession) -> Sequence[Channel]:
    query = select(Channel).order_by(Channel.sort_order, Channel.name)
    return (await session.scalars(query)).all()


async def get_channel(session: AsyncSession, channel_id: str) -> Channel:
    return await _get(session, Channel, channel_id)


async def _check_unique_name(
    session: AsyncSession, model: type[Area] | type[Channel], name: str, own_id: str | None = None
) -> None:
    existing = await session.scalar(select(model.id).where(model.name == name))
    if existing is not None and existing != own_id:
        raise ConflictError(f"{model.__tablename__} named {name!r} already exists")


async def create_area(
    session: AsyncSession, *, name: str, actor: str, icon: str | None = None
) -> Area:
    await _check_unique_name(session, Area, name)
    order = (await session.scalar(select(func.max(Area.sort_order)))) or 0
    return await _create(session, Area(name=name, icon=icon, sort_order=order + 1), actor)


async def update_area(
    session: AsyncSession, area_id: str, changes: dict[str, Any], actor: str
) -> Area:
    area = await _get(session, Area, area_id)
    if "name" in changes:
        await _check_unique_name(session, Area, changes["name"], area.id)
    return await _update(session, area, changes, actor)


async def delete_area(session: AsyncSession, area_id: str, actor: str) -> None:
    area = await _get(session, Area, area_id)
    count = await session.scalar(select(func.count()).where(Channel.area_id == area.id))
    if count:
        raise ConflictError(f"area still has {count} channel(s); move or delete them first")
    await _delete(session, area, actor)


async def create_channel(
    session: AsyncSession,
    *,
    name: str,
    area_id: str,
    actor: str,
    kind: ChannelKind = ChannelKind.COURSE,
    vault_path: str | None = None,
) -> Channel:
    if kind in (ChannelKind.SYSTEM, ChannelKind.PERSONAL):
        raise InvalidError(f"{kind} channels are built in and cannot be created")
    await _get(session, Area, area_id)
    await _check_unique_name(session, Channel, name)
    order = await session.scalar(
        select(func.max(Channel.sort_order)).where(Channel.area_id == area_id)
    )
    channel = Channel(
        name=name,
        area_id=area_id,
        kind=kind,
        vault_path=vault_path,
        sort_order=(order or 0) + 1,
    )
    return await _create(session, channel, actor)


async def update_channel(
    session: AsyncSession, channel_id: str, changes: dict[str, Any], actor: str
) -> Channel:
    channel = await get_channel(session, channel_id)
    if channel.kind == ChannelKind.SYSTEM:
        raise InvalidError("system channels cannot be changed")
    if changes.get("kind") in (ChannelKind.SYSTEM, ChannelKind.PERSONAL) or (
        channel.kind == ChannelKind.PERSONAL and changes.get("kind", channel.kind) != channel.kind
    ):
        raise InvalidError("built-in channel kinds cannot be changed")
    if "name" in changes:
        await _check_unique_name(session, Channel, changes["name"], channel.id)
    if changes.get("area_id") is not None:
        await _get(session, Area, changes["area_id"])
    return await _update(session, channel, changes, actor)


async def delete_channel(
    session: AsyncSession, channel_id: str, actor: str, *, force: bool = False
) -> None:
    """Refuses while the channel still holds tasks or events unless `force`; forced
    deletes log each contained object so nothing disappears without a trace."""
    channel = await get_channel(session, channel_id)
    if channel.kind in (ChannelKind.SYSTEM, ChannelKind.PERSONAL):
        raise InvalidError("built-in channels cannot be deleted")
    tasks = (await session.scalars(select(Task).where(Task.channel_id == channel.id))).all()
    events = (await session.scalars(select(Event).where(Event.channel_id == channel.id))).all()
    if (tasks or events) and not force:
        raise ConflictError(
            f"channel has {len(tasks)} task(s) and {len(events)} event(s); pass force to delete"
        )
    for obj in [*tasks, *events]:
        await _delete(session, obj, actor)
    await _delete(session, channel, actor)


# --- tasks --------------------------------------------------------------------


async def _column(session: AsyncSession, channel_id: str, status: TaskStatus) -> list[Task]:
    query = (
        select(Task)
        .where(Task.channel_id == channel_id, Task.status == status)
        .order_by(Task.position, Task.id)
    )
    return list((await session.scalars(query)).all())


async def _end_position(session: AsyncSession, channel_id: str, status: TaskStatus) -> float:
    query = select(func.max(Task.position)).where(
        Task.channel_id == channel_id, Task.status == status
    )
    last = (await session.execute(query)).scalar_one_or_none()
    return (last or 0.0) + POSITION_STEP


async def list_tasks(
    session: AsyncSession, channel_id: str | None = None, status: TaskStatus | None = None
) -> Sequence[Task]:
    query = select(Task).order_by(Task.status, Task.position, Task.id)
    if channel_id is not None:
        query = query.where(Task.channel_id == channel_id)
    if status is not None:
        query = query.where(Task.status == status)
    return (await session.scalars(query)).all()


async def get_task(session: AsyncSession, task_id: str) -> Task:
    return await _get(session, Task, task_id)


async def create_task(
    session: AsyncSession,
    *,
    channel_id: str,
    title: str,
    actor: str,
    description: str | None = None,
    status: TaskStatus = TaskStatus.TODO,
    due_at: datetime | None = None,
    priority: int | None = None,
) -> Task:
    await get_channel(session, channel_id)
    task = Task(
        channel_id=channel_id,
        title=title,
        description=description,
        status=status,
        position=await _end_position(session, channel_id, status),
        due_at=due_at,
        priority=priority,
    )
    return await _create(session, task, actor)


async def update_task(
    session: AsyncSession, task_id: str, changes: dict[str, Any], actor: str
) -> Task:
    """Field edits. A status or channel change appends the card to the end of its new
    column; use move_task to place it precisely."""
    task = await get_task(session, task_id)
    if "channel_id" in changes:
        await get_channel(session, changes["channel_id"])
    new_channel = changes.get("channel_id", task.channel_id)
    new_status = changes.get("status", task.status)
    if (new_channel, new_status) != (task.channel_id, task.status):
        changes = {**changes, "position": await _end_position(session, new_channel, new_status)}
    return await _update(session, task, changes, actor)


async def move_task(
    session: AsyncSession,
    task_id: str,
    *,
    status: TaskStatus,
    actor: str,
    after_id: str | None = None,
    before_id: str | None = None,
) -> Task:
    """Place a card in `status` right below `after_id` or right above `before_id`
    (neither: end of column). Position is the midpoint of its neighbours; when the gap
    gets too narrow the column is renumbered first."""
    if after_id is not None and before_id is not None:
        raise InvalidError("give after_id or before_id, not both")
    task = await get_task(session, task_id)
    column = [t for t in await _column(session, task.channel_id, status) if t.id != task.id]
    ids = [t.id for t in column]
    anchor = after_id or before_id
    if anchor is not None and anchor not in ids:
        raise InvalidError(f"anchor task {anchor} is not in column {status}")
    if after_id is not None:
        index = ids.index(after_id) + 1
    elif before_id is not None:
        index = ids.index(before_id)
    else:
        index = len(column)

    def neighbours() -> tuple[float | None, float | None]:
        prev = column[index - 1].position if index > 0 else None
        nxt = column[index].position if index < len(column) else None
        return prev, nxt

    prev, nxt = neighbours()
    if prev is not None and nxt is not None and nxt - prev <= MIN_POSITION_GAP:
        for i, t in enumerate(column, start=1):
            t.position = i * POSITION_STEP
        prev, nxt = neighbours()

    if nxt is None:
        position = (prev or 0.0) + POSITION_STEP
    elif prev is None:
        position = nxt - POSITION_STEP
    else:
        position = (prev + nxt) / 2

    return await _update(
        session, task, {"status": status, "position": position}, actor, action="moved"
    )


async def delete_task(session: AsyncSession, task_id: str, actor: str) -> None:
    await _delete(session, await get_task(session, task_id), actor)


# --- events -------------------------------------------------------------------


EVENT_TIME_FIELDS = ("starts_at", "ends_at", "start_date", "end_date")


def _check_event_times(
    starts_at: datetime | None,
    ends_at: datetime | None,
    start_date: date | None,
    end_date: date | None,
) -> dict[str, Any]:
    """Validates one of the two event shapes and returns the normalized time fields
    (an all-day event without end_date lasts one day)."""
    if starts_at is not None:
        if start_date is not None or end_date is not None:
            raise InvalidError("timed event cannot have start_date/end_date")
        if ends_at is not None and ends_at < starts_at:
            raise InvalidError("ends_at is before starts_at")
    elif start_date is not None:
        if ends_at is not None:
            raise InvalidError("all-day event cannot have ends_at")
        end_date = end_date or start_date + timedelta(days=1)
        if end_date <= start_date:
            raise InvalidError("end_date must be after start_date (end is exclusive)")
    else:
        raise InvalidError("event needs either starts_at (timed) or start_date (all-day)")
    return {
        "starts_at": starts_at,
        "ends_at": ends_at,
        "start_date": start_date,
        "end_date": end_date,
    }


async def get_event(session: AsyncSession, event_id: str) -> Event:
    return await _get(session, Event, event_id)


async def create_event(
    session: AsyncSession,
    *,
    channel_id: str,
    title: str,
    actor: str,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    location: str | None = None,
    rrule: str | None = None,
    calendar_id: str | None = None,
) -> Event:
    await get_channel(session, channel_id)
    times = _check_event_times(starts_at, ends_at, start_date, end_date)
    event = Event(
        channel_id=channel_id,
        title=title,
        location=location,
        rrule=rrule,
        calendar_id=calendar_id,
        **times,
    )
    return await _create(session, event, actor)


async def update_event(
    session: AsyncSession, event_id: str, changes: dict[str, Any], actor: str
) -> Event:
    event = await get_event(session, event_id)
    if "channel_id" in changes:
        await get_channel(session, changes["channel_id"])
    if any(key in changes for key in EVENT_TIME_FIELDS):
        current = {key: getattr(event, key) for key in EVENT_TIME_FIELDS}
        changes = changes | _check_event_times(**(current | changes))
    return await _update(session, event, changes, actor)


async def delete_event(session: AsyncSession, event_id: str, actor: str) -> None:
    await _delete(session, await get_event(session, event_id), actor)


async def list_events(
    session: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    tz: ZoneInfo,
    channel_id: str | None = None,
) -> Sequence[Event]:
    """Events overlapping [start, end). All-day events compare by local date."""
    first_day = start.astimezone(tz).date()
    last_day = (end - timedelta(microseconds=1)).astimezone(tz).date()
    timed = (
        Event.starts_at < end,
        or_(Event.ends_at > start, Event.ends_at.is_(None) & (Event.starts_at >= start)),
    )
    all_day = (Event.start_date <= last_day, Event.end_date > first_day)
    query = (
        select(Event)
        .where(or_(timed[0] & timed[1], all_day[0] & all_day[1]))
        .order_by(Event.start_date, Event.starts_at)
    )
    if channel_id is not None:
        query = query.where(Event.channel_id == channel_id)
    return (await session.scalars(query)).all()


# --- inbox --------------------------------------------------------------------


async def get_inbox_item(session: AsyncSession, item_id: str) -> InboxItem:
    return await _get(session, InboxItem, item_id)


async def create_inbox_item(
    session: AsyncSession,
    *,
    raw_text: str,
    captured_via: str,
    actor: str,
    channel_id: str | None = None,
    suggestion: dict[str, Any] | None = None,
) -> InboxItem:
    item = InboxItem(raw_text=raw_text, captured_via=captured_via, channel_id=channel_id)
    if suggestion is not None:
        item.suggestion_json = suggestion
        item.confidence = suggestion.get("confidence")
        item.status = InboxStatus.SUGGESTED
    return await _create(session, item, actor)


async def update_inbox_item(
    session: AsyncSession, item_id: str, changes: dict[str, Any], actor: str
) -> InboxItem:
    return await _update(session, await get_inbox_item(session, item_id), changes, actor)


async def delete_inbox_item(session: AsyncSession, item_id: str, actor: str) -> None:
    await _delete(session, await get_inbox_item(session, item_id), actor)


async def list_inbox(
    session: AsyncSession,
    *,
    statuses: Sequence[InboxStatus] | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> tuple[Sequence[InboxItem], str | None]:
    """Newest first. `cursor` is the opaque value returned as next_cursor."""
    query = select(InboxItem).order_by(InboxItem.created_at.desc(), InboxItem.id.desc())
    if statuses:
        query = query.where(InboxItem.status.in_(statuses))
    if cursor is not None:
        last_ts, last_id = _decode_cursor(cursor)
        query = query.where(
            or_(
                InboxItem.created_at < last_ts,
                (InboxItem.created_at == last_ts) & (InboxItem.id < last_id),
            )
        )
    items = (await session.scalars(query.limit(limit + 1))).all()
    if len(items) <= limit:
        return items, None
    last = items[limit - 1]
    return items[:limit], f"{last.created_at.isoformat()}|{last.id}"


async def accept_inbox_item(
    session: AsyncSession, item_id: str, *, actor: str, overrides: dict[str, Any] | None = None
) -> Task | Event:
    """Turns a (suggested) inbox item into a task or event. `overrides` are the user's
    corrections ("고치기") on top of the suggestion; messages that showed the item now
    point at the new object (PLAN P4)."""
    item = await get_inbox_item(session, item_id)
    if item.status == InboxStatus.ACCEPTED:
        raise ConflictError("inbox item was already accepted")
    fields: dict[str, Any] = {
        k: v for k, v in (item.suggestion_json or {}).items() if k != "error"
    } | (overrides or {})
    kind = fields.get("type") or "task"
    title = (fields.get("title") or item.raw_text).strip()[:500]
    channel_id = await _resolve_channel(session, fields, item.channel_id)

    obj: Task | Event
    if kind in ("task", "idea"):
        obj = await create_task(
            session,
            channel_id=channel_id,
            title=title,
            actor=actor,
            status=TaskStatus.BACKLOG if kind == "idea" else TaskStatus.TODO,
            due_at=_as_datetime(fields.get("due_at")),
            description=fields.get("summary") or None,
        )
    elif kind == "event":
        start_date = _as_date(fields.get("start_date") or fields.get("all_day_date"))
        obj = await create_event(
            session,
            channel_id=channel_id,
            title=title,
            actor=actor,
            starts_at=None if start_date else _as_datetime(fields.get("starts_at")),
            ends_at=None if start_date else _as_datetime(fields.get("ends_at")),
            start_date=start_date,
            end_date=_as_date(fields.get("end_date")),
        )
    else:
        raise InvalidError("공부 노트는 옵시디언 연동 후 파일로 만들 수 있어요")

    result = {"object_type": obj.__tablename__, "id": obj.id}
    await _update(
        session,
        item,
        {
            "status": InboxStatus.ACCEPTED,
            "suggestion_json": (item.suggestion_json or {}) | {"result": result},
        },
        actor,
    )
    await _repoint_messages(session, "inbox_item", item.id, obj, actor)
    return obj


async def _resolve_channel(
    session: AsyncSession, fields: dict[str, Any], captured_in: str | None
) -> str:
    """Explicit channel_id, else the classifier's channel_hint by name, else the channel
    the text was typed in; text typed into #today/#inbox with no better guess goes to
    the personal #일상 channel."""
    if fields.get("channel_id"):
        return (await get_channel(session, fields["channel_id"])).id
    if hint := fields.get("channel_hint"):
        by_name = await session.scalar(select(Channel).where(Channel.name == hint))
        if by_name is not None and by_name.kind != ChannelKind.SYSTEM:
            return by_name.id
    if captured_in is not None:
        channel = await session.get(Channel, captured_in)
        if channel is not None and channel.kind != ChannelKind.SYSTEM:
            return channel.id
    personal = await get_personal_channel(session)
    if personal is not None:
        return personal.id
    raise InvalidError("어느 채널에 넣을지 골라 주세요")


async def get_personal_channel(session: AsyncSession) -> Channel | None:
    return await session.scalar(select(Channel).where(Channel.kind == ChannelKind.PERSONAL))


def _as_datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise InvalidError("time without timezone offset")
    return parsed


def _as_date(value: Any) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


# --- messages -------------------------------------------------------------------


async def get_message(session: AsyncSession, message_id: str) -> Message:
    return await _get(session, Message, message_id)


async def create_message(
    session: AsyncSession,
    *,
    channel_id: str,
    body: str,
    actor: str,
    author_type: AuthorType = AuthorType.USER,
    author_id: str | None = None,
    thread_root_id: str | None = None,
    ref: Record | None = None,
) -> Message:
    await get_channel(session, channel_id)
    if thread_root_id is not None:
        root = await get_message(session, thread_root_id)
        if root.channel_id != channel_id:
            raise InvalidError("thread root is in another channel")
        thread_root_id = root.thread_root_id or root.id  # threads are one level deep
    message = Message(
        channel_id=channel_id,
        body=body,
        author_type=author_type,
        author_id=author_id,
        thread_root_id=thread_root_id,
        ref_type=ref.__tablename__ if ref is not None else None,
        ref_id=ref.id if ref is not None else None,
    )
    return await _create(session, message, actor)


async def update_message(
    session: AsyncSession, message_id: str, changes: dict[str, Any], actor: str
) -> Message:
    return await _update(session, await get_message(session, message_id), changes, actor)


async def list_messages(
    session: AsyncSession, channel_id: str, *, cursor: str | None = None, limit: int = 50
) -> tuple[list[Message], str | None]:
    """Top-level messages, oldest first; `next_cursor` fetches the page before this one."""
    query = (
        select(Message)
        .where(Message.channel_id == channel_id, Message.thread_root_id.is_(None))
        .order_by(Message.created_at.desc(), Message.id.desc())
    )
    if cursor is not None:
        last_ts, last_id = _decode_cursor(cursor)
        query = query.where(
            or_(
                Message.created_at < last_ts,
                (Message.created_at == last_ts) & (Message.id < last_id),
            )
        )
    rows = list((await session.scalars(query.limit(limit + 1))).all())
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = f"{rows[-1].created_at.isoformat()}|{rows[-1].id}"
    return rows[::-1], next_cursor


async def list_thread(session: AsyncSession, root_id: str) -> tuple[Message, list[Message]]:
    root = await get_message(session, root_id)
    query = (
        select(Message)
        .where(Message.thread_root_id == root.id)
        .order_by(Message.created_at, Message.id)
    )
    return root, list((await session.scalars(query)).all())


async def reply_counts(session: AsyncSession, root_ids: Sequence[str]) -> dict[str, int]:
    if not root_ids:
        return {}
    query = (
        select(Message.thread_root_id, func.count())
        .where(Message.thread_root_id.in_(root_ids))
        .group_by(Message.thread_root_id)
    )
    return {str(root): count for root, count in (await session.execute(query)).all()}


async def convert_message(
    session: AsyncSession,
    message_id: str,
    *,
    kind: str,
    actor: str,
    fields: dict[str, Any] | None = None,
) -> Task | Event:
    """Quick actions 🗂/📅 on a message: make it a task or event. A message that is still
    an inbox suggestion goes through accept_inbox_item so the item is closed too."""
    message = await get_message(session, message_id)
    if message.ref_type == "inbox_item" and message.ref_id:
        return await accept_inbox_item(
            session, message.ref_id, actor=actor, overrides={"type": kind, **(fields or {})}
        )
    if message.ref_type in ("task", "event"):
        raise ConflictError("message already points at a task or event")
    data = {"title": message.body.strip()[:500]} | (fields or {})
    obj: Task | Event
    if kind == "task":
        obj = await create_task(
            session,
            channel_id=message.channel_id,
            title=data["title"],
            actor=actor,
            due_at=_as_datetime(data.get("due_at")),
        )
    elif kind == "event":
        obj = await create_event(
            session,
            channel_id=message.channel_id,
            title=data["title"],
            actor=actor,
            starts_at=_as_datetime(data.get("starts_at")),
            ends_at=_as_datetime(data.get("ends_at")),
            start_date=_as_date(data.get("start_date")),
            end_date=_as_date(data.get("end_date")),
        )
    else:
        raise InvalidError(f"cannot convert a message into {kind!r}")
    await _update(session, message, {"ref_type": obj.__tablename__, "ref_id": obj.id}, actor)
    return obj


async def _repoint_messages(
    session: AsyncSession, ref_type: str, ref_id: str, target: Record, actor: str
) -> None:
    query = select(Message).where(Message.ref_type == ref_type, Message.ref_id == ref_id)
    for message in (await session.scalars(query)).all():
        await _update(
            session, message, {"ref_type": target.__tablename__, "ref_id": target.id}, actor
        )


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        raw_ts, last_id = cursor.split("|", 1)
        return datetime.fromisoformat(raw_ts), last_id
    except ValueError as exc:
        raise InvalidError("malformed cursor") from exc


# --- today --------------------------------------------------------------------

OPEN_INBOX = (InboxStatus.NEW, InboxStatus.SUGGESTED)


async def get_today(
    session: AsyncSession, *, now: datetime, tz: ZoneInfo, due_soon_days: int
) -> dict[str, Any]:
    """Today's events, open tasks overdue or due within `due_soon_days`, open inbox count."""
    day_start = datetime.combine(now.astimezone(tz).date(), time(), tzinfo=tz)
    events = await list_events(session, start=day_start, end=day_start + timedelta(days=1), tz=tz)
    due_query = (
        select(Task)
        .where(
            Task.status != TaskStatus.DONE,
            Task.due_at.is_not(None),
            Task.due_at < day_start + timedelta(days=due_soon_days + 1),
        )
        .order_by(Task.due_at)
    )
    due_tasks = (await session.scalars(due_query)).all()
    count_query = select(func.count()).where(InboxItem.status.in_(OPEN_INBOX))
    inbox_count = (await session.execute(count_query)).scalar_one()
    return {"events": events, "due_tasks": due_tasks, "inbox_count": inbox_count}


# --- routines -------------------------------------------------------------------

STREAK_LOOKBACK_DAYS = 400


def _weekdays(value: str) -> str:
    digits = sorted(set(value))
    if not digits or any(d not in "0123456" for d in digits):
        raise InvalidError("weekdays must be digits 0 (Mon) … 6 (Sun), e.g. 01234")
    return "".join(digits)


def _scheduled(routine: Routine, day: date) -> bool:
    return str(day.weekday()) in routine.weekdays


def streak(routine: Routine, done_days: set[date], today: date) -> int:
    """Consecutive scheduled days done, counting back from today. Days the routine
    rests (weekdays not listed) are skipped; today not done yet does not break it."""
    count = 0
    day = today
    first = routine.created_at.date() - timedelta(days=STREAK_LOOKBACK_DAYS)
    while day >= first:
        if _scheduled(routine, day):
            if day in done_days:
                count += 1
            elif day != today:
                break
        day -= timedelta(days=1)
    return count


async def list_routines(
    session: AsyncSession, *, day: date, today: date
) -> list[tuple[Routine, bool, bool, int]]:
    """(routine, scheduled on `day`, done on `day`, streak as of `today`) per routine."""
    routines = (
        await session.scalars(select(Routine).order_by(Routine.sort_order, Routine.created_at))
    ).all()
    since = today - timedelta(days=STREAK_LOOKBACK_DAYS)
    checks = (
        await session.execute(
            select(RoutineCheck.routine_id, RoutineCheck.day).where(
                RoutineCheck.day >= min(since, day)
            )
        )
    ).all()
    done: dict[str, set[date]] = {}
    for routine_id, check_day in checks:
        done.setdefault(routine_id, set()).add(check_day)
    return [
        (
            r,
            _scheduled(r, day),
            day in done.get(r.id, set()),
            streak(r, done.get(r.id, set()), today),
        )
        for r in routines
    ]


async def create_routine(
    session: AsyncSession, *, title: str, actor: str, weekdays: str = "0123456"
) -> Routine:
    order = (await session.scalar(select(func.max(Routine.sort_order)))) or 0
    routine = Routine(title=title, weekdays=_weekdays(weekdays), sort_order=order + 1)
    return await _create(session, routine, actor)


async def update_routine(
    session: AsyncSession, routine_id: str, changes: dict[str, Any], actor: str
) -> Routine:
    if "weekdays" in changes:
        changes = changes | {"weekdays": _weekdays(changes["weekdays"])}
    return await _update(session, await _get(session, Routine, routine_id), changes, actor)


async def delete_routine(session: AsyncSession, routine_id: str, actor: str) -> None:
    """Also drops its check history (FK cascade)."""
    await _delete(session, await _get(session, Routine, routine_id), actor)


async def set_routine_check(
    session: AsyncSession, routine_id: str, *, day: date, done: bool, today: date, actor: str
) -> None:
    routine = await _get(session, Routine, routine_id)
    if day > today:
        raise InvalidError("미래 날짜는 체크할 수 없어요")
    if not _scheduled(routine, day):
        raise InvalidError("이 루틴을 쉬는 요일이에요")
    check = await session.scalar(
        select(RoutineCheck).where(RoutineCheck.routine_id == routine.id, RoutineCheck.day == day)
    )
    if done and check is None:
        await _create(session, RoutineCheck(routine_id=routine.id, day=day), actor)
    elif not done and check is not None:
        await _delete(session, check, actor)


# --- seed ---------------------------------------------------------------------


def load_seed(seed_path: Path) -> dict[str, Any]:
    if not seed_path.exists():
        return {}
    return tomllib.loads(seed_path.read_text(encoding="utf-8"))


async def seed_defaults(session: AsyncSession, config: dict[str, Any]) -> None:
    """Ensures the system channels, then loads the demo areas/channels from the seed
    config only into an empty database: afterwards the user owns the list and a
    deleted course must not come back on restart."""
    for order, name in enumerate(SYSTEM_CHANNELS, start=-len(SYSTEM_CHANNELS)):
        exists = await session.scalar(select(Channel.id).where(Channel.name == name))
        if exists is None:
            channel = Channel(name=name, kind=ChannelKind.SYSTEM, sort_order=order)
            await _create(session, channel, "system")
    # Found by kind, not name, so renaming #일상 sticks across restarts.
    if await get_personal_channel(session) is None:
        taken = await session.scalar(select(Channel.id).where(Channel.name == PERSONAL_CHANNEL))
        name = PERSONAL_CHANNEL if taken is None else f"{PERSONAL_CHANNEL} (개인)"
        await _create(
            session, Channel(name=name, kind=ChannelKind.PERSONAL, sort_order=0), "system"
        )

    has_user_data = await session.scalar(select(Area.id).limit(1)) is not None
    if has_user_data:
        return
    for area_order, area_cfg in enumerate(config.get("area", [])):
        area = await _create(
            session,
            Area(name=area_cfg["name"], icon=area_cfg.get("icon"), sort_order=area_order),
            "system",
        )
        for order, ch in enumerate(area_cfg.get("channel", [])):
            channel = Channel(
                name=ch["name"],
                kind=ChannelKind(ch.get("kind", ChannelKind.COURSE)),
                sort_order=order,
                area_id=area.id,
                vault_path=ch.get("vault_path"),
            )
            await _create(session, channel, "system")
