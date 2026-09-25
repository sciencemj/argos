"""MCP server (PLAN Phase 4): the app's data as tools for Hermes, Claude Code and Codex.

Served over Streamable HTTP inside the API process (`/mcp`), so writes go through the
same domain services and reach open browser tabs over the WebSocket hub. Every tool
call is attributed to the calling agent: `/mcp?agent=claude` or `X-Argos-Agent: claude`
→ actor `agent:claude`. Deletions never run directly; they become approval requests.
"""

import asyncio
import functools
import re
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from argos import chat, services, vault
from argos.config import Settings
from argos.models import Channel, ChannelKind, Event, InboxStatus, Task, TaskStatus

INSTRUCTIONS = """Argos is the user's single source of truth for schedules and tasks.
When the user mentions a task, deadline or appointment, record it here with these tools
instead of keeping it in your own memory. Times are Asia/Seoul unless an offset is given.
Channels are courses/projects; use list_channels to find names. "일상" holds everyday
items that belong to no course. Deleting always waits for the user's approval."""

_AGENT = re.compile(r"^[a-z0-9_-]{1,40}$")
STATUSES = [s.value for s in TaskStatus]
# Filled as tools register; the agent form offers these (PLAN Phase 10).
TOOL_NAMES: list[str] = []
DESTRUCTIVE = {"delete_task", "delete_event"}  # always go through approval
READ_ONLY = {
    "list_channels", "get_today", "get_schedule", "search_notes", "list_tasks",
    "list_inbox", "get_course_progress",
}  # fmt: skip


def _agent(ctx: Context) -> str:
    request = ctx.request_context.request
    raw = ""
    if request is not None:
        raw = request.query_params.get("agent") or request.headers.get("x-argos-agent") or ""
    raw = raw.strip().lower()
    return f"agent:{raw if _AGENT.match(raw) else 'unknown'}"


def build_mcp(app: FastAPI) -> MCPServer:
    """Tools read `app.state` at call time (sessionmaker, settings, classifier)."""
    mcp = MCPServer("argos", instructions=INSTRUCTIONS)

    @asynccontextmanager
    async def session() -> AsyncGenerator[AsyncSession]:
        async with app.state.sessionmaker() as s:
            yield s

    def settings() -> Settings:
        return app.state.settings

    async def run[T](work: Callable[[AsyncSession], Awaitable[T]]) -> T:
        """Domain errors become tool errors the agent can read and act on."""
        async with session() as s:
            try:
                return await work(s)
            except (services.NotFoundError, services.InvalidError, services.ConflictError) as exc:
                raise ToolError(str(exc)) from exc

    async def allow(ctx: Context, name: str) -> None:
        """Custom agents may only use the tools on their list (PLAN Phase 10); built-in
        agents (no list) may use all. Destructive tools still need approval."""
        caller = _agent(ctx).removeprefix("agent:")
        async with session() as s:
            agent = await services.find_agent(s, caller)
        if agent is not None and agent.tools_json is not None and name not in agent.tools_json:
            raise ToolError(
                f"'{agent.display_name}' 에이전트에게 허용되지 않은 도구예요: {name}. "
                "필요하면 사용자에게 에이전트 설정에서 허용해 달라고 하세요."
            )

    def tool[**P, R](fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        """@mcp.tool() behind the whitelist check. Every tool takes `ctx`."""

        @functools.wraps(fn)
        async def guarded(*args: P.args, **kwargs: P.kwargs) -> R:
            ctx = cast(Context, kwargs.get("ctx"))
            await allow(ctx, fn.__name__)
            return await fn(*args, **kwargs)

        mcp.tool()(guarded)  # pyright: ignore[reportUnknownArgumentType]
        if fn.__name__ not in TOOL_NAMES:
            TOOL_NAMES.append(fn.__name__)
        return fn

    # --- reads -----------------------------------------------------------------------

    @tool
    async def list_channels(ctx: Context) -> list[dict[str, Any]]:
        """Channels (courses, projects, 일상, inbox) with their names and kinds."""

        async def work(s: AsyncSession) -> list[dict[str, Any]]:
            areas = {a.id: a.name for a in await services.list_areas(s)}
            return [
                {"id": c.id, "name": c.name, "kind": c.kind, "area": areas.get(c.area_id or "")}
                for c in await services.list_channels(s)
                if c.kind != ChannelKind.DM and (c.kind != ChannelKind.SYSTEM or c.name == "inbox")
            ]

        return await run(work)

    @tool
    async def get_today(ctx: Context) -> dict[str, Any]:
        """Today's events, open tasks due soon or overdue, open inbox count, and routines."""
        tz = settings().zoneinfo

        async def work(s: AsyncSession) -> dict[str, Any]:
            now = datetime.now(UTC)
            today = await services.get_today(
                s, now=now, tz=tz, due_soon_days=settings().due_soon_days
            )
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

        return await run(work)

    @tool
    async def get_schedule(
        ctx: Context, start: str, end: str, channel: str | None = None
    ) -> list[dict[str, Any]]:
        """Events between start and end (YYYY-MM-DD or ISO datetime; end exclusive)."""
        tz = settings().zoneinfo

        async def work(s: AsyncSession) -> list[dict[str, Any]]:
            channel_id = (await _channel(s, channel)).id if channel else None
            events = await services.list_events(
                s,
                start=_instant(start, tz),
                end=_instant(end, tz),
                tz=tz,
                channel_id=channel_id,
            )
            names = await _channel_names(s)
            return [_event(e, names, tz) for e in events]

        return await run(work)

    @tool
    async def search_notes(
        ctx: Context, query: str, channel: str | None = None, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Search the user's Obsidian notes (title and body). Returns titles, vault paths
        and a snippet with the match in [brackets]; use it to ground answers in their notes."""

        async def work(s: AsyncSession) -> list[dict[str, Any]]:
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

        return await run(work)

    @tool
    async def list_tasks(
        ctx: Context,
        channel: str | None = None,
        status: str | None = None,
        include_done: bool = False,
    ) -> list[dict[str, Any]]:
        """Tasks, optionally for one channel or status (backlog, todo, in_progress, review,
        done). Done tasks are left out unless include_done or status="done"."""
        tz = settings().zoneinfo

        async def work(s: AsyncSession) -> list[dict[str, Any]]:
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

        return await run(work)

    @tool
    async def list_inbox(ctx: Context) -> list[dict[str, Any]]:
        """Captured text not yet sorted into a task or event, newest first."""
        tz = settings().zoneinfo

        async def work(s: AsyncSession) -> list[dict[str, Any]]:
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

        return await run(work)

    @tool
    async def get_course_progress(ctx: Context, channel: str | None = None) -> list[dict[str, Any]]:
        """Per course/project (or one channel): task counts by status and the next deadline."""
        tz = settings().zoneinfo

        async def work(s: AsyncSession) -> list[dict[str, Any]]:
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

        return await run(work)

    # --- writes ----------------------------------------------------------------------

    @tool
    async def add_task(
        ctx: Context,
        title: str,
        channel: str,
        due: str | None = None,
        description: str | None = None,
        status: str = "todo",
        priority: int | None = None,
    ) -> dict[str, Any]:
        """Add a task to a channel. due: YYYY-MM-DD (→ 23:59) or ISO datetime.
        priority: 0 low … 3 urgent."""
        actor, tz = _agent(ctx), settings().zoneinfo

        async def work(s: AsyncSession) -> dict[str, Any]:
            target = await _channel(s, channel)
            task = await services.create_task(
                s,
                channel_id=target.id,
                title=title,
                description=description,
                status=_status(status),
                due_at=_due(due, tz),
                priority=_priority(priority),
                actor=actor,
            )
            await _announce(s, target, f"할 일을 추가했어요: {task.title}", task, actor)
            return _task(task, {target.id: target.name}, tz)

        return await run(work)

    @tool
    async def update_task(
        ctx: Context,
        task_id: str,
        title: str | None = None,
        description: str | None = None,
        due: str | None = None,
        priority: int | None = None,
        channel: str | None = None,
    ) -> dict[str, Any]:
        """Edit a task's fields. due="none" clears the deadline. Use move_task for status."""
        actor, tz = _agent(ctx), settings().zoneinfo

        async def work(s: AsyncSession) -> dict[str, Any]:
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
            task = await services.update_task(s, task_id, changes, actor)
            return _task(task, await _channel_names(s), tz)

        return await run(work)

    @tool
    async def move_task(
        ctx: Context,
        task_id: str,
        status: str,
        after_task_id: str | None = None,
        before_task_id: str | None = None,
    ) -> dict[str, Any]:
        """Move a task to a kanban column (backlog, todo, in_progress, review, done),
        optionally right after/before another task in that column."""
        actor, tz = _agent(ctx), settings().zoneinfo

        async def work(s: AsyncSession) -> dict[str, Any]:
            task = await services.move_task(
                s,
                task_id,
                status=_status(status),
                after_id=after_task_id,
                before_id=before_task_id,
                actor=actor,
            )
            return _task(task, await _channel_names(s), tz)

        return await run(work)

    @tool
    async def create_event(
        ctx: Context,
        title: str,
        channel: str,
        start: str,
        end: str | None = None,
        location: str | None = None,
    ) -> dict[str, Any]:
        """Add an event. start/end as ISO datetimes (end defaults to +1h), or as dates
        YYYY-MM-DD for an all-day event (end = last day, inclusive)."""
        actor, tz = _agent(ctx), settings().zoneinfo

        async def work(s: AsyncSession) -> dict[str, Any]:
            target = await _channel(s, channel)
            event = await services.create_event(
                s,
                channel_id=target.id,
                title=title,
                location=location,
                actor=actor,
                **_event_times(start, end, tz),
            )
            await _announce(s, target, f"일정을 추가했어요: {event.title}", event, actor)
            return _event(event, {target.id: target.name}, tz)

        return await run(work)

    @tool
    async def update_event(
        ctx: Context,
        event_id: str,
        title: str | None = None,
        start: str | None = None,
        end: str | None = None,
        location: str | None = None,
    ) -> dict[str, Any]:
        """Edit an event. Giving start replaces its time (same rules as create_event)."""
        actor, tz = _agent(ctx), settings().zoneinfo

        async def work(s: AsyncSession) -> dict[str, Any]:
            changes: dict[str, Any] = {}
            if title is not None:
                changes["title"] = title
            if location is not None:
                changes["location"] = location or None
            if start is not None:
                changes |= _event_times(start, end, tz)
            elif end is not None:
                raise services.InvalidError("give start together with end")
            event = await services.update_event(s, event_id, changes, actor)
            return _event(event, await _channel_names(s), tz)

        return await run(work)

    @tool
    async def capture_note(
        ctx: Context,
        text: str,
        channel: str | None = None,
    ) -> dict[str, Any]:
        """Drop free text into the inbox (e.g. something the user said in passing); Argos
        classifies it and the user confirms. Prefer add_task/create_event when clear."""
        actor = _agent(ctx)

        async def work(s: AsyncSession) -> dict[str, Any]:
            channel_id = (await _channel(s, channel)).id if channel else None
            item = await services.create_inbox_item(
                s,
                raw_text=text,
                captured_via=f"mcp:{actor.removeprefix('agent:')}",
                channel_id=channel_id,
                actor=actor,
            )
            return {"inbox_item_id": item.id, "status": item.status}

        result = await run(work)
        classifier = app.state.classifier
        if classifier is not None:
            _background(
                chat.classify_item(
                    app.state.sessionmaker,
                    classifier,
                    result["inbox_item_id"],
                    settings(),
                    datetime.now(UTC),
                )
            )
        return result

    # --- destructive: approval only (PLAN P5) ----------------------------------------

    @tool
    async def delete_task(
        ctx: Context,
        task_id: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Ask to delete a task. Nothing is deleted until the user approves in Argos."""
        return await _ask(ctx, "delete_task", {"task_id": task_id}, reason)

    @tool
    async def delete_event(
        ctx: Context,
        event_id: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Ask to delete an event. Nothing is deleted until the user approves in Argos."""
        return await _ask(ctx, "delete_event", {"event_id": event_id}, reason)

    async def _ask(
        ctx: Context, action: str, payload: dict[str, str], reason: str | None
    ) -> dict[str, Any]:
        actor = _agent(ctx)

        async def work(s: AsyncSession) -> dict[str, Any]:
            approval = await services.request_approval(
                s, action=action, payload=payload, actor=actor, reason=reason
            )
            return {
                "approval_id": approval.id,
                "status": "pending",
                "message": "사용자 승인을 기다리는 중이에요. 승인 전에는 삭제되지 않아요.",
            }

        return await run(work)

    return mcp


# --- helpers ------------------------------------------------------------------------

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
