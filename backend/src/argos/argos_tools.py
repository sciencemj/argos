"""The Argos domain tools (PLAN Phase 4, v0.2 Stage 1.2), written once for both callers:
the MCP server (`mcp_server.py`, external agents) and the native harness registry
(`harness.core_tools`).

Each tool is a plain async function `(ctx: ToolContext, **args) -> JSON-able`. Its
signature and docstring are the contract: the MCP server publishes them as the tool's
schema, and `tool_def()` turns them into a JSON Schema plus an argument-validating handler
for the native harness. Writes go through `services` like every other write path.

Deletions differ by caller: over MCP they only create an approval request (the external
agent cannot be paused); in the native harness they are `destructive` tools the run
itself waits on (`ApprovalNeeded`), so once approved they delete directly.
"""

import asyncio
import inspect
import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, get_type_hints
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ValidationError, create_model
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from argos import chat, course_lms, knowledge, services, vault
from argos.harness import ToolContext, ToolDef, ToolKind, ToolRegistry
from argos.models import Channel, ChannelKind, Event, InboxStatus, Task, TaskStatus

STATUSES = [s.value for s in TaskStatus]
DOMAIN_ERRORS = (services.NotFoundError, services.InvalidError, services.ConflictError)


@dataclass(frozen=True)
class ArgosTool:
    name: str
    kind: ToolKind
    fn: Callable[..., Awaitable[Any]]
    description: str
    params: tuple[inspect.Parameter, ...]  # without ctx
    args_model: type[BaseModel]
    mcp: bool = True  # also served by the MCP server


ARGOS_TOOLS: dict[str, ArgosTool] = {}


def argos_tool(kind: ToolKind, *, mcp: bool = True) -> Callable[[Any], Any]:
    def decorate(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        hints = get_type_hints(fn)
        params = tuple(
            p.replace(annotation=hints.get(p.name, Any))
            for p in list(inspect.signature(fn).parameters.values())[1:]
        )
        fields: dict[str, Any] = {
            p.name: (p.annotation, ... if p.default is inspect.Parameter.empty else p.default)
            for p in params
        }
        model = create_model(f"{fn.__name__}_args", **fields)
        ARGOS_TOOLS[fn.__name__] = ArgosTool(
            fn.__name__, kind, fn, inspect.cleandoc(fn.__doc__ or ""), params, model, mcp
        )
        return fn

    return decorate


def tool_def(tool: ArgosTool) -> ToolDef:
    """The native harness form: JSON Schema from the signature, arguments validated
    (and coerced) by the same model, result as JSON text."""

    async def handler(args: dict[str, Any], ctx: ToolContext) -> str:
        try:
            parsed = tool.args_model.model_validate(args)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()
            )
            raise ValueError(f"인자 오류 — {problems}") from None
        kwargs = {p.name: getattr(parsed, p.name) for p in tool.params}
        result = await tool.fn(ctx, **kwargs)
        return json.dumps(result, ensure_ascii=False, default=str)

    schema = tool.args_model.model_json_schema()
    schema.pop("title", None)
    return ToolDef(
        name=tool.name,
        description=tool.description,
        parameters=schema,
        kind=tool.kind,
        handler=handler,
        summarize=_SUMMARIES.get(tool.name),
    )


def register_argos_tools(registry: ToolRegistry) -> None:
    """Puts every Argos tool into `registry` (again on each app start: replaces)."""
    for tool in ARGOS_TOOLS.values():
        registry.register(tool_def(tool), replace=True)


@asynccontextmanager
async def _session(ctx: ToolContext) -> AsyncGenerator[AsyncSession]:
    if ctx.sessionmaker is None:
        raise RuntimeError("this tool needs the Argos database")
    async with ctx.sessionmaker() as s:
        yield s


def _tz(ctx: ToolContext) -> ZoneInfo:
    return ctx.settings.zoneinfo if ctx.settings is not None else ZoneInfo("Asia/Seoul")


# --- reads -------------------------------------------------------------------------------


@argos_tool("read")
async def list_channels(ctx: ToolContext) -> list[dict[str, Any]]:
    """Channels (courses, projects, and the two parts of 내 공간) with their names and kinds."""
    async with _session(ctx) as s:
        areas = {a.id: a.name for a in await services.list_areas(s)}
        return [
            {"id": c.id, "name": c.name, "kind": c.kind, "area": areas.get(c.area_id or "")}
            for c in await services.list_channels(s)
            if c.kind != ChannelKind.DM and (c.kind != ChannelKind.SYSTEM or c.name == "inbox")
        ]


@argos_tool("read")
async def get_today(ctx: ToolContext) -> dict[str, Any]:
    """Today's events, open tasks due soon or overdue, open inbox count, and routines."""
    tz = _tz(ctx)
    async with _session(ctx) as s:
        now = datetime.now(UTC)
        soon = ctx.settings.due_soon_days if ctx.settings is not None else 3
        today = await services.get_today(s, now=now, tz=tz, due_soon_days=soon)
        names = await _channel_names(s)
        local_day = now.astimezone(tz).date()
        routines = await services.list_routines(s, day=local_day, today=local_day)
        return {
            "date": local_day.isoformat(),
            "events": [_event(e, names, tz) for e in today["events"]],
            "due_tasks": [_task(t, names, tz) for t in today["due_tasks"]],
            "open_inbox": today["inbox_count"],
            "routines": [
                {"title": r.title, "done": done, "streak": streak}
                for r, scheduled, done, streak in routines
                if scheduled
            ],
        }


@argos_tool("read")
async def get_schedule(
    ctx: ToolContext, start: str, end: str, channel: str | None = None
) -> list[dict[str, Any]]:
    """Events between start and end (YYYY-MM-DD or ISO datetime; end exclusive)."""
    tz = _tz(ctx)
    async with _session(ctx) as s:
        channel_id = (await _channel(s, channel)).id if channel else None
        events = await services.list_events(
            s, start=_instant(start, tz), end=_instant(end, tz), tz=tz, channel_id=channel_id
        )
        names = await _channel_names(s)
        return [_event(e, names, tz) for e in events]


@argos_tool("read")
async def search_notes(
    ctx: ToolContext, query: str, channel: str | None = None, limit: int = 10
) -> list[dict[str, Any]]:
    """Search the user's Obsidian notes (title and body). Returns titles, vault paths
    and a snippet with the match in [brackets]; use it to ground answers in their notes."""
    async with _session(ctx) as s:
        channel_id = (await _channel(s, channel)).id if channel else None
        names = await _channel_names(s)
        found = await vault.search(s, query, channel_id, max(1, min(limit, 30)))
        return [
            {
                "title": n.title,
                "path": n.vault_path,
                "channel": names.get(n.channel_id or ""),
                "snippet": snippet,
            }
            for n, snippet in found
        ]


@argos_tool("read")
async def search_course_lms(
    ctx: ToolContext,
    query: str = "",
    channel: str | None = None,
    kind: str | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Search a course's LMS assignments and notices imported into Argos (assignment
    descriptions with submission guidance, announcements, messages, materials). Best
    matches first; an empty query lists the newest. `channel`: the course (all courses
    when omitted); `kind`: assignment, announcement, activity, conversation,
    attendance or material. Cite the title, date and url of what you use."""
    tz = _tz(ctx)
    async with _session(ctx) as s:
        channel_ids = [(await _channel(s, channel)).id] if channel else None
        docs = await course_lms.course_docs(s, channel_ids)
        if kind:
            docs = [d for d in docs if d.kind == kind]
        names = await _channel_names(s)
        return [
            {
                "kind": d.kind,
                "title": d.title,
                "channel": names.get(d.channel_id),
                "date": _local(d.date, tz),
                "url": d.url,
                "summary": d.summary,
                "text": d.text[:4000],
            }
            for d in course_lms.rank(docs, query, max(1, min(limit, 20)))
        ]


@argos_tool("read")
async def search_knowledge(
    ctx: ToolContext, query: str, channel: str | None = None, limit: int = 8
) -> list[dict[str, Any]]:
    """Search the text of files in Argos: LMS course materials (lecture PDFs, notes) and
    files the user uploaded to a channel. Returns the file name, channel, where it is
    and a snippet with the match in [brackets]. `channel`: one course/project (all when
    omitted). Use read_file on an uploaded file's path to read more of it."""
    async with _session(ctx) as s:
        channel_id = (await _channel(s, channel)).id if channel else None
        names = await _channel_names(s)
        hits = await knowledge.search(s, query, channel_id, max(1, min(limit, 30)))
        return [
            {
                "title": h.title,
                "source": h.source,
                "channel": names.get(h.channel_id or ""),
                "path": h.path,
                "snippet": h.snippet,
            }
            for h in hits
        ]


@argos_tool("read")
async def list_tasks(
    ctx: ToolContext,
    channel: str | None = None,
    status: str | None = None,
    include_done: bool = False,
) -> list[dict[str, Any]]:
    """Tasks, optionally for one channel or status (backlog, todo, in_progress, review,
    done). Done tasks are left out unless include_done or status="done"."""
    tz = _tz(ctx)
    async with _session(ctx) as s:
        channel_id = (await _channel(s, channel)).id if channel else None
        tasks = await services.list_tasks(
            s, channel_id=channel_id, status=_status(status) if status else None
        )
        names = await _channel_names(s)
        return [
            _task(t, names, tz)
            for t in tasks
            if include_done or status or t.status != TaskStatus.DONE
        ]


@argos_tool("read")
async def list_inbox(ctx: ToolContext) -> list[dict[str, Any]]:
    """Captured text not yet sorted into a task or event, newest first."""
    tz = _tz(ctx)
    async with _session(ctx) as s:
        items, _ = await services.list_inbox(
            s, statuses=[InboxStatus.NEW, InboxStatus.SUGGESTED], limit=100
        )
        return [
            {
                "id": i.id,
                "text": i.raw_text,
                "status": i.status,
                "suggestion": i.suggestion_json,
                "captured_at": _local(i.created_at, tz),
            }
            for i in items
        ]


@argos_tool("read")
async def get_course_progress(ctx: ToolContext, channel: str | None = None) -> list[dict[str, Any]]:
    """Per course/project (or one channel): task counts by status and the next deadline."""
    tz = _tz(ctx)
    async with _session(ctx) as s:
        if channel:
            targets = [await _channel(s, channel)]
        else:
            targets = [
                c
                for c in await services.list_channels(s)
                if c.kind not in (ChannelKind.SYSTEM, ChannelKind.DM)
            ]
        out: list[dict[str, Any]] = []
        for c in targets:
            tasks = await services.list_tasks(s, channel_id=c.id)
            open_due = sorted(
                (t for t in tasks if t.due_at and t.status != TaskStatus.DONE),
                key=lambda t: t.due_at or datetime.max.replace(tzinfo=UTC),
            )
            out.append(
                {
                    "channel": c.name,
                    "kind": c.kind,
                    "counts": {st: sum(t.status == st for t in tasks) for st in STATUSES},
                    "next_deadline": (
                        {"title": open_due[0].title, "due_at": _local(open_due[0].due_at, tz)}
                        if open_due
                        else None
                    ),
                }
            )
        return out


# --- writes ------------------------------------------------------------------------------


@argos_tool("write")
async def create_channel(
    ctx: ToolContext, name: str, kind: str = "course", area: str | None = None
) -> dict[str, Any]:
    """Create a channel for a new course (kind "course") or project (kind "project").
    area: an area name from list_channels; default: the area that already holds that
    kind of channel, else the first area."""
    if kind not in ("course", "project"):
        raise services.InvalidError('kind must be "course" or "project"')
    channel_kind = ChannelKind(kind)
    async with _session(ctx) as s:
        areas = await services.list_areas(s)
        target = None
        if area:
            target = next((a for a in areas if a.name == area.strip().lstrip("#")), None)
            if target is None:
                raise services.InvalidError(f"no area named {area!r}: {[a.name for a in areas]}")
        else:
            used = {c.area_id for c in await services.list_channels(s) if c.kind == channel_kind}
            target = next((a for a in areas if a.id in used), areas[0] if areas else None)
        if target is None:
            target = await services.create_area(
                s, name="학업" if kind == "course" else "프로젝트", icon=None, actor=ctx.actor
            )
        channel = await services.create_channel(
            s, name=name.strip().lstrip("#"), area_id=target.id, kind=channel_kind, actor=ctx.actor
        )
        return {"id": channel.id, "name": channel.name, "kind": kind, "area": target.name}


@argos_tool("write")
async def add_task(
    ctx: ToolContext,
    title: str,
    channel: str,
    due: str | None = None,
    description: str | None = None,
    status: str = "todo",
    priority: int | None = None,
) -> dict[str, Any]:
    """Add a task to a channel. due: YYYY-MM-DD (→ 23:59) or ISO datetime.
    priority: 0 low … 3 urgent."""
    tz = _tz(ctx)
    async with _session(ctx) as s:
        target = await _channel(s, channel)
        task = await services.create_task(
            s,
            channel_id=target.id,
            title=title,
            description=description,
            status=_status(status),
            due_at=_due(due, tz),
            priority=_priority(priority),
            actor=ctx.actor,
        )
        await _announce(s, target, f"할 일을 추가했어요: {task.title}", task, ctx.actor)
        return _task(task, {target.id: target.name}, tz)


@argos_tool("write")
async def update_task(
    ctx: ToolContext,
    task_id: str,
    title: str | None = None,
    description: str | None = None,
    due: str | None = None,
    priority: int | None = None,
    channel: str | None = None,
) -> dict[str, Any]:
    """Edit a task's fields. due="none" clears the deadline. Use move_task for status."""
    tz = _tz(ctx)
    async with _session(ctx) as s:
        changes: dict[str, Any] = {}
        if title is not None:
            changes["title"] = title
        if description is not None:
            changes["description"] = description or None
        if due is not None:
            changes["due_at"] = None if due.lower() in ("", "none", "null") else _due(due, tz)
        if priority is not None:
            changes["priority"] = _priority(priority)
        if channel is not None:
            changes["channel_id"] = (await _channel(s, channel)).id
        task = await services.update_task(s, task_id, changes, ctx.actor)
        return _task(task, await _channel_names(s), tz)


@argos_tool("write")
async def move_task(
    ctx: ToolContext,
    task_id: str,
    status: str,
    after_task_id: str | None = None,
    before_task_id: str | None = None,
) -> dict[str, Any]:
    """Move a task to a kanban column (backlog, todo, in_progress, review, done),
    optionally right after/before another task in that column."""
    tz = _tz(ctx)
    async with _session(ctx) as s:
        task = await services.move_task(
            s,
            task_id,
            status=_status(status),
            after_id=after_task_id,
            before_id=before_task_id,
            actor=ctx.actor,
        )
        return _task(task, await _channel_names(s), tz)


@argos_tool("write")
async def create_event(
    ctx: ToolContext,
    title: str,
    channel: str,
    start: str,
    end: str | None = None,
    location: str | None = None,
) -> dict[str, Any]:
    """Add an event. start/end as ISO datetimes (end defaults to +1h), or as dates
    YYYY-MM-DD for an all-day event (end = last day, inclusive)."""
    tz = _tz(ctx)
    async with _session(ctx) as s:
        target = await _channel(s, channel)
        event = await services.create_event(
            s,
            channel_id=target.id,
            title=title,
            location=location,
            actor=ctx.actor,
            **_event_times(start, end, tz),
        )
        await _announce(s, target, f"일정을 추가했어요: {event.title}", event, ctx.actor)
        return _event(event, {target.id: target.name}, tz)


@argos_tool("write")
async def update_event(
    ctx: ToolContext,
    event_id: str,
    title: str | None = None,
    start: str | None = None,
    end: str | None = None,
    location: str | None = None,
    channel: str | None = None,
) -> dict[str, Any]:
    """Edit an event. Giving start replaces its time (same rules as create_event);
    channel (name or id, see list_channels) moves it to another channel."""
    tz = _tz(ctx)
    async with _session(ctx) as s:
        changes: dict[str, Any] = {}
        if title is not None:
            changes["title"] = title
        if location is not None:
            changes["location"] = location or None
        if channel is not None:
            changes["channel_id"] = (await _channel(s, channel)).id
        if start is not None:
            changes |= _event_times(start, end, tz)
        elif end is not None:
            raise services.InvalidError("give start together with end")
        event = await services.update_event(s, event_id, changes, ctx.actor)
        return _event(event, await _channel_names(s), tz)


@argos_tool("write")
async def remember(ctx: ToolContext, text: str) -> dict[str, Any]:
    """Save something to your long-term memory (a preference, a fact about the user or
    their work) so later conversations know it. One short fact per call. The default
    assistant writes Argos's main memory; every other agent keeps its own."""
    async with _session(ctx) as s:
        memory = await services.remember(s, services.memory_scope(ctx.agent), text, ctx.actor)
        return {"memory_id": memory.id, "text": memory.text}


@argos_tool("read")
async def recall(ctx: ToolContext, query: str | None = None) -> list[dict[str, Any]]:
    """Your saved memories (newest last), optionally only those containing `query`."""
    async with _session(ctx) as s:
        rows = await services.list_memories(s, services.memory_scope(ctx.agent), limit=200)
    key = (query or "").strip().lower()
    return [
        {"memory_id": m.id, "text": m.text, "saved": m.created_at.date().isoformat()}
        for m in rows
        if not key or key in m.text.lower()
    ]


@argos_tool("write")
async def capture_note(ctx: ToolContext, text: str, channel: str | None = None) -> dict[str, Any]:
    """Drop free text into the inbox (e.g. something the user said in passing); Argos
    classifies it and the user confirms. Prefer add_task/create_event when clear."""
    async with _session(ctx) as s:
        channel_id = (await _channel(s, channel)).id if channel else None
        item = await services.create_inbox_item(
            s,
            raw_text=text,
            captured_via=f"mcp:{ctx.agent}",
            channel_id=channel_id,
            actor=ctx.actor,
        )
        result = {"inbox_item_id": item.id, "status": item.status}
    if ctx.classifier is not None and ctx.sessionmaker is not None and ctx.settings is not None:
        _background(
            chat.classify_item(
                ctx.sessionmaker, ctx.classifier, item.id, ctx.settings, datetime.now(UTC)
            )
        )
    return result


# --- destructive -------------------------------------------------------------------------
# The native harness asks the user before running these (kind "destructive"); the MCP
# server serves its own approval-request versions instead (mcp_server._ask).


@argos_tool("destructive")
async def delete_task(ctx: ToolContext, task_id: str, reason: str | None = None) -> dict[str, Any]:
    """Delete a task. The user approves it in Argos before it runs."""
    async with _session(ctx) as s:
        title = (await services.get_task(s, task_id)).title
        await services.delete_task(s, task_id, ctx.actor)
    return {"deleted": task_id, "title": title}


@argos_tool("destructive")
async def delete_event(
    ctx: ToolContext, event_id: str, reason: str | None = None
) -> dict[str, Any]:
    """Delete an event. The user approves it in Argos before it runs."""
    async with _session(ctx) as s:
        title = (await services.get_event(s, event_id)).title
        await services.delete_event(s, event_id, ctx.actor)
    return {"deleted": event_id, "title": title}


def _reason(args: dict[str, Any], line: str) -> str:
    reason = str(args.get("reason") or "").strip()
    return f"{line} — {reason}" if reason else line


_SUMMARIES: dict[str, Callable[[dict[str, Any]], str]] = {
    "delete_task": lambda a: _reason(a, f"할 일 삭제: {a.get('task_id')}"),
    "delete_event": lambda a: _reason(a, f"일정 삭제: {a.get('event_id')}"),
}


# --- helpers -----------------------------------------------------------------------------

_tasks: set[asyncio.Task[None]] = set()


def _background(coro: Awaitable[None]) -> None:
    task = asyncio.ensure_future(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _channel(s: AsyncSession, ref: str | None) -> Channel:
    if not ref:
        raise services.InvalidError("channel is required (see list_channels)")
    name = ref.strip().lstrip("#").strip()
    found = await s.scalar(select(Channel).where((Channel.id == name) | (Channel.name == name)))
    if found is None:
        raise services.NotFoundError("channel", ref)
    return found


async def _channel_names(s: AsyncSession) -> dict[str, str]:
    return {c.id: c.name for c in await services.list_channels(s)}


async def _announce(
    s: AsyncSession, channel: Channel, body: str, obj: Task | Event, actor: str
) -> None:
    """Agent writes show up in the channel feed as the agent's message (PLAN P4)."""
    await services.create_message(
        s,
        channel_id=channel.id,
        body=body,
        author_type=services.AuthorType.AGENT,
        author_id=actor.removeprefix("agent:"),
        ref=obj,
        actor=actor,
    )


def _status(value: str) -> TaskStatus:
    try:
        return TaskStatus(value)
    except ValueError as exc:
        raise services.InvalidError(f"status must be one of {', '.join(STATUSES)}") from exc


def _priority(value: int | None) -> int | None:
    if value is not None and not 0 <= value <= 3:
        raise services.InvalidError("priority must be 0 (low) … 3 (urgent)")
    return value


def _parse(value: str, tz: ZoneInfo) -> date | datetime:
    try:
        if len(value) == 10:
            return date.fromisoformat(value)
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise services.InvalidError(f"not a date or ISO datetime: {value!r}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=tz)


def _instant(value: str, tz: ZoneInfo) -> datetime:
    parsed = _parse(value, tz)
    return parsed if isinstance(parsed, datetime) else datetime.combine(parsed, time(), tz)


def _due(value: str | None, tz: ZoneInfo) -> datetime | None:
    if not value:
        return None
    parsed = _parse(value, tz)
    return parsed if isinstance(parsed, datetime) else datetime.combine(parsed, time(23, 59), tz)


def _event_times(start: str, end: str | None, tz: ZoneInfo) -> dict[str, Any]:
    first = _parse(start, tz)
    if not isinstance(first, datetime):
        last = _parse(end, tz) if end else first
        if isinstance(last, datetime):
            raise services.InvalidError("an all-day event needs date-only start and end")
        return {
            "start_date": first,
            "end_date": last + timedelta(days=1),
            "starts_at": None,
            "ends_at": None,
        }
    finish = _parse(end, tz) if end else first + timedelta(hours=1)
    if not isinstance(finish, datetime):
        raise services.InvalidError("a timed event needs an ISO datetime end")
    return {"starts_at": first, "ends_at": finish, "start_date": None, "end_date": None}


def _local(value: datetime | None, tz: ZoneInfo) -> str | None:
    return value.astimezone(tz).isoformat() if value else None


def _task(t: Task, names: dict[str, str], tz: ZoneInfo) -> dict[str, Any]:
    return {
        "id": t.id,
        "title": t.title,
        "channel": names.get(t.channel_id),
        "status": t.status,
        "due_at": _local(t.due_at, tz),
        "priority": t.priority,
        "description": t.description,
    }


def _event(e: Event, names: dict[str, str], tz: ZoneInfo) -> dict[str, Any]:
    out: dict[str, Any] = {"id": e.id, "title": e.title, "channel": names.get(e.channel_id)}
    if e.start_date is not None:
        last = (e.end_date or e.start_date + timedelta(days=1)) - timedelta(days=1)
        out |= {
            "all_day": True,
            "start_date": e.start_date.isoformat(),
            "end_date": last.isoformat(),
        }
    else:
        out |= {
            "all_day": False,
            "starts_at": _local(e.starts_at, tz),
            "ends_at": _local(e.ends_at, tz),
        }
    if e.location:
        out["location"] = e.location
    return out


def mcp_tools() -> list[ArgosTool]:
    return [t for t in ARGOS_TOOLS.values() if t.mcp]
