"""Argos speaking first (PLAN Phase 11): deadline notices, daily roundups, the weekly
review, numbers about how work goes, and the morning briefing.

Every notice has a `key` so it happens once: a task due in three days gets one "D-3"
notice, one "D-1" notice and, if it slips, one "마감 지남"; a changed due date is a new
key. Notices show in the app (list, badge, browser notification) and, when the user
turns it on, go to a messenger through `hermes send` (no LLM involved)."""

import asyncio
import json
import logging
import math
import shutil
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from argos import services
from argos.config import Settings
from argos.hub import hub
from argos.models import (
    ActivityLog,
    AuthorType,
    Channel,
    ChannelKind,
    InboxItem,
    InboxStatus,
    Notification,
    Task,
    TaskStatus,
)

log = logging.getLogger(__name__)

OPEN_INBOX = (InboxStatus.NEW, InboxStatus.SUGGESTED)
OVERDUE_LOOKBACK = timedelta(days=7)  # older misses are not news any more
WEEKDAY_KO = "월화수목금토일"


@dataclass
class Candidate:
    key: str
    kind: str
    title: str
    body: str | None = None
    object_type: str | None = None
    object_id: str | None = None
    channel_id: str | None = None


def _local_date(value: datetime, tz: ZoneInfo) -> date:
    return value.astimezone(tz).date()


async def deadline_candidates(
    session: AsyncSession, now: datetime, tz: ZoneInfo, language: str = "ko"
) -> list[Candidate]:
    """D-3, D-1 (today or tomorrow) and passed deadlines of open tasks."""
    today = now.astimezone(tz).date()
    rows = await session.scalars(
        select(Task).where(
            Task.status != TaskStatus.DONE,
            Task.due_at.is_not(None),
            Task.due_at > now - OVERDUE_LOOKBACK,
            Task.due_at < now + timedelta(days=4),
        )
    )
    names = {c.id: c.name for c in (await session.scalars(select(Channel))).all()}
    found: list[Candidate] = []
    for task in rows.all():
        assert task.due_at is not None
        stamp = task.due_at.isoformat()
        when = task.due_at.astimezone(tz)
        where = f"#{names.get(task.channel_id, '?')}"
        days = (_local_date(task.due_at, tz) - today).days
        if task.due_at <= now:
            title = f"Overdue · {task.title}" if language == "en" else f"마감 지남 · {task.title}"
            key, kind = f"overdue:{task.id}:{stamp}", "overdue"
        elif days <= 1:
            label = (
                ("Today" if days == 0 else "Tomorrow")
                if language == "en"
                else ("오늘" if days == 0 else "내일")
            )
            key, kind, title = (
                f"due:{task.id}:D-1:{stamp}",
                "due_soon",
                f"{label} due · {task.title}"
                if language == "en"
                else f"{label} 마감 · {task.title}",
            )
        elif days <= 3:
            key, kind, title = f"due:{task.id}:D-3:{stamp}", "due_soon", f"D-{days} · {task.title}"
        else:
            continue
        weekday = when.strftime("%a") if language == "en" else WEEKDAY_KO[when.weekday()]
        body = f"{where} · {when:%m/%d} ({weekday}) {when:%H:%M}"
        found.append(Candidate(key, kind, title, body, "task", task.id, task.channel_id))
    return found


async def roundup_candidates(
    session: AsyncSession, now: datetime, settings: Settings
) -> list[Candidate]:
    """Once a day after the digest hour: a stale inbox, and open tasks with no date."""
    tz = settings.zoneinfo
    local = now.astimezone(tz)
    if local.hour < settings.notify_digest_hour:
        return []
    day = local.date().isoformat()
    found: list[Candidate] = []
    stale = (
        await session.scalars(
            select(InboxItem).where(
                InboxItem.status.in_(OPEN_INBOX),
                InboxItem.created_at < now - timedelta(hours=settings.inbox_stale_hours),
            )
        )
    ).all()
    if stale:
        sample = " / ".join(i.raw_text.strip()[:30] for i in stale[:3])
        stale_title = (
            f"{len(stale)} Inbox items have waited over {settings.inbox_stale_hours} hours"
            if settings.language == "en"
            else f"인박스에 {settings.inbox_stale_hours}시간 넘게 정리 안 된 항목 {len(stale)}개"
        )
        found.append(
            Candidate(
                f"inbox:{day}",
                "inbox_stale",
                stale_title,
                sample,
                "inbox",
            )
        )
    undated = (
        await session.scalars(
            select(Task).where(
                Task.status.in_([TaskStatus.TODO, TaskStatus.IN_PROGRESS]),
                Task.due_at.is_(None),
                Task.created_at < now - timedelta(days=settings.undated_after_days),
            )
        )
    ).all()
    if undated:
        sample = " / ".join(t.title[:30] for t in undated[:3])
        found.append(
            Candidate(
                f"undated:{day}",
                "undated",
                f"{len(undated)} tasks have no due date · Set one so they don't slip by"
                if settings.language == "en"
                else f"날짜 없는 할 일 {len(undated)}개 · 마감을 정하면 놓치지 않아요",
                sample,
                "task",
            )
        )
    return found


async def record(session: AsyncSession, candidates: Sequence[Candidate]) -> list[Notification]:
    """Stores the candidates not seen before; returns the new notices."""
    if not candidates:
        return []
    keys = [c.key for c in candidates]
    seen = set(
        (await session.scalars(select(Notification.key).where(Notification.key.in_(keys)))).all()
    )
    fresh: list[Notification] = []
    for c in candidates:
        if c.key in seen:
            continue
        seen.add(c.key)
        notice = Notification(
            key=c.key,
            kind=c.kind,
            title=c.title,
            body=c.body,
            object_type=c.object_type,
            object_id=c.object_id,
            channel_id=c.channel_id,
        )
        session.add(notice)
        fresh.append(notice)
    await session.commit()
    for notice in fresh:
        await hub.publish(
            "notification.created",
            {"object_type": "notification", "id": notice.id, "title": notice.title,
             "body": notice.body, "kind": notice.kind},
        )  # fmt: skip
    return fresh


# --- messenger delivery (Hermes) ----------------------------------------------------------


async def hermes_send(settings: Settings, target: str, text: str) -> str | None:
    """Sends through `hermes send` (the gateway's own delivery, no LLM). Returns an
    error message, or None when it went out."""
    binary = shutil.which(settings.hermes_bin)
    if binary is None:
        return (
            f"{settings.hermes_bin} command not found"
            if settings.language == "en"
            else f"{settings.hermes_bin} 명령을 찾을 수 없어요"
        )
    try:
        process = await asyncio.create_subprocess_exec(
            binary, "send", "--to", target, "--quiet", "--subject", "[Argos]", text,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )  # fmt: skip
        _, err = await asyncio.wait_for(process.communicate(), 60)
    except TimeoutError:
        return (
            "Hermes delivery timed out"
            if settings.language == "en"
            else "Hermes 전송이 시간 안에 끝나지 않았어요"
        )
    except OSError as exc:
        return (
            f"Could not start Hermes ({type(exc).__name__})"
            if settings.language == "en"
            else f"Hermes를 실행하지 못했어요 ({type(exc).__name__})"
        )
    if process.returncode != 0:
        detail = err.decode(errors="replace").strip().splitlines()[-1:] or [""]
        prefix = "Hermes delivery failed" if settings.language == "en" else "Hermes 전송 실패"
        return f"{prefix} ({process.returncode}): {detail[0][:200]}"
    return None


async def hermes_targets(settings: Settings) -> list[dict[str, str]]:
    """Where Hermes can deliver (`hermes send --list --json`): each platform's home
    channel, then its channels."""
    binary = shutil.which(settings.hermes_bin)
    if binary is None:
        return []
    process = await asyncio.create_subprocess_exec(
        binary, "send", "--list", "--json",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )  # fmt: skip
    try:
        out, _ = await asyncio.wait_for(process.communicate(), 30)
        data = cast(dict[str, Any], json.loads(out or b"{}"))
    except (TimeoutError, ValueError):
        return []
    targets: list[dict[str, str]] = []
    for platform, entries in cast(dict[str, Any], data.get("platforms") or {}).items():
        label = "default channel" if settings.language == "en" else "기본 채널"
        targets.append({"target": platform, "label": f"{platform} · {label}"})
        for entry in cast(list[dict[str, Any]], entries or []):
            if entry.get("type") != "channel" or not entry.get("id"):
                continue
            where = entry.get("guild") or ""
            name = entry.get("name") or entry["id"]
            targets.append(
                {"target": f"{platform}:{entry['id']}", "label": f"{platform} · {where} #{name}"}
            )
    return targets


def notice_text(notice: Notification) -> str:
    return notice.title + (f"\n{notice.body}" if notice.body else "")


# --- weekly review ------------------------------------------------------------------------


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


async def done_between(
    session: AsyncSession, start: datetime, end: datetime
) -> list[tuple[str, datetime]]:
    """(task id, when it became done) from the activity log."""
    rows = await session.scalars(
        select(ActivityLog).where(
            ActivityLog.object_type == "task",
            ActivityLog.created_at >= start,
            ActivityLog.created_at < end,
        )
    )
    done: dict[str, datetime] = {}
    for row in rows.all():
        after = row.after_json or {}
        if after.get("status") == TaskStatus.DONE:
            done[row.object_id] = row.created_at
    return list(done.items())


async def idea_groups(
    texts: list[str], settings: Settings, embed: Callable[[list[str]], Any] | None = None
) -> list[list[str]]:
    """Inbox notes that say similar things, grouped by embedding similarity (Ollama).
    Similarity scales differ between models, so the bar comes from the notes
    themselves: average-linkage merging while groups are more alike than the mean
    pair plus half a standard deviation. No embedding model, or too few notes: none."""
    if len(texts) < 3:
        return []
    try:
        vectors: list[list[float]] = await (embed or _ollama_embed(settings))(texts)
    except Exception as exc:
        log.info("idea grouping skipped: %s", exc)
        return []

    def cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
        return dot / norm if norm else 0.0

    n = len(vectors)
    sim = [[cosine(vectors[i], vectors[j]) for j in range(n)] for i in range(n)]
    pairs = [sim[i][j] for i in range(n) for j in range(i + 1, n)]
    mean = sum(pairs) / len(pairs)
    spread = math.sqrt(sum((p - mean) ** 2 for p in pairs) / len(pairs))
    bar = mean + 0.5 * spread
    clusters: list[list[int]] = [[i] for i in range(n)]
    while len(clusters) > 1:
        best, pair = -1.0, (0, 0)
        for x in range(len(clusters)):
            for y in range(x + 1, len(clusters)):
                links = [sim[i][j] for i in clusters[x] for j in clusters[y]]
                average = sum(links) / len(links)
                if average > best:
                    best, pair = average, (x, y)
        if best < bar or spread < 1e-6:
            break
        x, y = pair
        clusters[x] += clusters.pop(y)
    return [[texts[i] for i in sorted(c)] for c in clusters if len(c) >= 2]


def _ollama_embed(settings: Settings) -> Callable[[list[str]], Any]:
    async def embed(texts: list[str]) -> list[list[float]]:
        base = settings.classifier_base_url or "http://127.0.0.1:11434/v1"
        client = AsyncOpenAI(base_url=base, api_key="ollama", timeout=60, max_retries=0)
        model = settings.review_embed_model or await _find_embed_model(base)
        if not model:
            raise LookupError("no embedding model installed")
        # EmbeddingGemma separates topics far better with its clustering prompt.
        prefix = "task: clustering | query: " if "embeddinggemma" in model else ""
        response = await client.embeddings.create(model=model, input=[prefix + t for t in texts])
        return [d.embedding for d in response.data]

    return embed


async def _find_embed_model(base: str) -> str | None:
    import httpx2

    root = base.removesuffix("/v1")
    async with httpx2.AsyncClient(timeout=5) as http:
        tags = (await http.get(f"{root}/api/tags")).json()
    for model in cast(list[dict[str, Any]], tags.get("models") or []):
        name = str(model.get("name", ""))
        if "embed" in name:
            return name
    return None


async def weekly_review(
    session: AsyncSession,
    now: datetime,
    settings: Settings,
    embed: Callable[[list[str]], Any] | None = None,
) -> str:
    """The review as Markdown: this week's done cards, backlog gone stale, grouped
    inbox ideas and a few numbers."""
    tz = settings.zoneinfo
    start_day = week_start(now.astimezone(tz).date())
    start = datetime.combine(start_day, time(), tz).astimezone(UTC)
    names = {c.id: c.name for c in (await session.scalars(select(Channel))).all()}
    done_ids = dict(await done_between(session, start, now))
    done_tasks: Sequence[Task] = []
    if done_ids:
        done_tasks = (await session.scalars(select(Task).where(Task.id.in_(done_ids)))).all()
    en = settings.language == "en"
    heading = "Weekly Review" if en else "주간 리뷰"
    lines = [f"# {heading} · {start_day:%m/%d} – {now.astimezone(tz):%m/%d}", ""]
    lines.append(
        f"## Completed this week · {len(done_tasks)} tasks"
        if en
        else f"## 이번 주 끝낸 일 {len(done_tasks)}개"
    )
    by_channel: dict[str, list[str]] = defaultdict(list)
    for task in done_tasks:
        by_channel[names.get(task.channel_id, "?")].append(task.title)
    if not done_tasks:
        lines.append(
            "- Nothing yet. Start with one small task next week."
            if en
            else "- 아직 없어요. 다음 주엔 작은 것부터 하나씩!"
        )
    for channel, titles in sorted(by_channel.items(), key=lambda kv: -len(kv[1])):
        extra = len(titles) - 5
        shown = ", ".join(titles[:5]) + (
            (f" and {extra} more" if en else f" 외 {extra}개") if extra > 0 else ""
        )
        lines.append(
            f"- **#{channel}** {len(titles)} tasks: {shown}"
            if en
            else f"- **#{channel}** {len(titles)}개: {shown}"
        )

    stale_since = now - timedelta(days=settings.backlog_stale_days)
    stale = (
        await session.scalars(
            select(Task)
            .where(Task.status == TaskStatus.BACKLOG, Task.updated_at < stale_since)
            .order_by(Task.updated_at)
            .limit(8)
        )
    ).all()
    lines += [
        "",
        f"## Backlog older than {settings.backlog_stale_days} days · {len(stale)} tasks"
        if en
        else f"## {settings.backlog_stale_days}일 넘게 그대로인 backlog {len(stale)}개",
    ]
    if stale:
        for task in stale:
            age = (now - task.updated_at).days
            lines.append(
                f"- {task.title} (#{names.get(task.channel_id, '?')}, {age} days)"
                if en
                else f"- {task.title} (#{names.get(task.channel_id, '?')}, {age}일째)"
            )
        lines.append(
            "→ Move these to To do, set a due date, or remove them if no longer needed."
            if en
            else "→ 할 일로 옮기거나, 날짜를 정하거나, 이제 필요 없으면 지워 보세요."
        )
    else:
        lines.append("- None. All clear." if en else "- 없어요. 깔끔해요.")

    ideas = (
        await session.scalars(
            select(InboxItem).where(InboxItem.status.in_(OPEN_INBOX)).order_by(InboxItem.created_at)
        )
    ).all()
    groups = await idea_groups([i.raw_text.strip() for i in ideas][:60], settings, embed)
    if groups:
        lines += ["", "## Related Inbox notes" if en else "## 비슷한 인박스 메모"]
        for n, group in enumerate(groups, 1):
            lines.append(
                (f"- Group {n}: " if en else f"- 묶음 {n}: ")
                + " / ".join(t[:40] for t in group[:5])
            )
        lines.append(
            "→ Consider combining these into one task or note."
            if en
            else "→ 한 할 일이나 노트로 합쳐 보세요."
        )

    stats = await analytics(session, now, tz)
    if stats["processing"]:
        lines += [
            "",
            "## Average completion time by channel (last 30 days)"
            if en
            else "## 과목별 평균 처리 시간 (최근 30일)",
        ]
        for row in stats["processing"][:6]:
            lines.append(
                f"- #{row['channel']}: {row['hours'] / 24:.1f} days ({row['count']} tasks)"
                if en
                else f"- #{row['channel']}: {row['hours'] / 24:.1f}일 ({row['count']}개)"
            )
    return "\n".join(lines)


async def post_review(
    session: AsyncSession, now: datetime, settings: Settings
) -> Notification | None:
    """Writes this week's review into #today once (key per ISO week)."""
    year, week, _ = now.astimezone(settings.zoneinfo).isocalendar()
    key = f"review:{year}-W{week:02d}"
    if await session.scalar(select(Notification.id).where(Notification.key == key)):
        return None
    text = await weekly_review(session, now, settings)
    today = await session.scalar(
        select(Channel).where(Channel.name == "today", Channel.kind == ChannelKind.SYSTEM)
    )
    message = None
    if today is not None:
        message = await services.create_message(
            session, channel_id=today.id, body=text, author_type=AuthorType.SYSTEM, actor="system"
        )
    [notice] = await record(
        session,
        [
            Candidate(
                key,
                "weekly_review",
                "Your weekly review is ready"
                if settings.language == "en"
                else "이번 주 리뷰가 도착했어요",
                text.split("\n")[2] if len(text.split("\n")) > 2 else None,
                "message" if message else None,
                message.id if message else None,
                today.id if today else None,
            )
        ],
    ) or [None]
    return notice


def review_due(now: datetime, settings: Settings) -> bool:
    local = now.astimezone(settings.zoneinfo)
    target = settings.weekly_review_weekday
    return local.weekday() > target or (
        local.weekday() == target and local.hour >= settings.weekly_review_hour
    )


# --- numbers --------------------------------------------------------------------------------


async def analytics(session: AsyncSession, now: datetime, tz: ZoneInfo) -> dict[str, Any]:
    """Average time from creation to done per channel (last 30 days) and done counts
    for the last 8 weeks (Monday-based), from the activity log."""
    since = now - timedelta(days=56)
    done = await done_between(session, since, now)
    ids = [task_id for task_id, _ in done]
    tasks = {t.id: t for t in (await session.scalars(select(Task).where(Task.id.in_(ids)))).all()}
    names = {c.id: c.name for c in (await session.scalars(select(Channel))).all()}
    per_channel: dict[str, list[float]] = defaultdict(list)
    weekly: dict[date, int] = defaultdict(int)
    for task_id, finished in done:
        weekly[week_start(finished.astimezone(tz).date())] += 1
        task = tasks.get(task_id)
        if task is not None and finished >= now - timedelta(days=30):
            hours = (finished - task.created_at).total_seconds() / 3600
            per_channel[names.get(task.channel_id, "?")].append(max(hours, 0))
    this_week = week_start(now.astimezone(tz).date())
    weeks = [this_week - timedelta(weeks=n) for n in range(7, -1, -1)]
    processing = sorted(
        ((ch, sum(v) / len(v), len(v)) for ch, v in per_channel.items()),
        key=lambda row: -row[2],
    )
    return {
        "processing": [{"channel": c, "hours": h, "count": n} for c, h, n in processing],
        "weekly_done": [{"week": w.isoformat(), "count": weekly.get(w, 0)} for w in weeks],
    }


# --- the background loop ------------------------------------------------------------------


class Notifier:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Callable[[], Settings],
        send: Callable[[Settings, str, str], Any] = hermes_send,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._settings = settings
        self.send = send
        self._loop: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self.last_run: datetime | None = None

    async def run(self, now: datetime | None = None) -> list[Notification]:
        """One pass: new notices, the weekly review when due, messenger delivery."""
        async with self._lock:
            now = now or datetime.now(UTC)
            config = self._settings()
            async with self._sessionmaker() as session:
                fresh = await record(
                    session,
                    [
                        *await deadline_candidates(session, now, config.zoneinfo, config.language),
                        *await roundup_candidates(session, now, config),
                    ],
                )
                if review_due(now, config) and (review := await post_review(session, now, config)):
                    fresh.append(review)
                if config.notify_hermes_target and fresh:
                    await self._deliver(session, fresh, config)
            self.last_run = now
            return fresh

    async def _deliver(
        self, session: AsyncSession, notices: list[Notification], config: Settings
    ) -> None:
        assert config.notify_hermes_target
        for notice in notices:
            error = await self.send(config, config.notify_hermes_target, notice_text(notice))
            notice.sent_at = None if error else datetime.now(UTC)
            notice.send_error = error
        await session.commit()

    def start(self) -> None:
        async def loop() -> None:
            while True:
                try:
                    await self.run()
                except Exception:
                    log.exception("notification pass failed")
                await asyncio.sleep(self._settings().notify_interval_minutes * 60)

        if self._settings().notify_interval_minutes > 0:
            self._loop = asyncio.create_task(loop())

    async def stop(self) -> None:
        if self._loop is not None:
            self._loop.cancel()
            try:
                await self._loop
            except asyncio.CancelledError:
                pass


# --- morning briefing (for other tools) --------------------------------------------------


async def briefing(session: AsyncSession, now: datetime, settings: Settings) -> dict[str, Any]:
    tz = settings.zoneinfo
    today = await services.get_today(session, now=now, tz=tz, due_soon_days=settings.due_soon_days)
    local = now.astimezone(tz)
    routines = await services.list_routines(session, day=local.date(), today=local.date())
    names = {c.id: c.name for c in (await session.scalars(select(Channel))).all()}

    def when(value: datetime | None) -> str | None:
        return value.astimezone(tz).isoformat() if value else None

    return {
        "date": local.date().isoformat(),
        "weekday": WEEKDAY_KO[local.weekday()],
        "events": [
            {
                "title": e.title,
                "channel": names.get(e.channel_id),
                "all_day": e.all_day,
                "starts_at": when(e.starts_at),
                "ends_at": when(e.ends_at),
                "location": e.location,
            }
            for e in today["events"]
        ],
        "due_tasks": [
            {
                "title": t.title,
                "channel": names.get(t.channel_id),
                "due_at": when(t.due_at),
                "overdue": bool(t.due_at and t.due_at < now),
            }
            for t in today["due_tasks"]
        ],
        "open_inbox": today["inbox_count"],
        "routines": [
            {"title": routine.title, "done": done}
            for routine, scheduled, done, _streak in routines
            if scheduled
        ],
    }


def briefing_markdown(data: dict[str, Any]) -> str:
    lines = [f"# {data['date']} ({data['weekday']}) 브리핑", "", "## 일정"]
    events = cast(list[dict[str, Any]], data["events"])
    for e in events:
        at = "종일" if e["all_day"] else str(e["starts_at"] or "")[11:16]
        where = f" (#{e['channel']})" if e["channel"] else ""
        lines.append(f"- {at} {e['title']}{where}")
    if not events:
        lines.append("- 없음")
    lines += ["", "## 마감"]
    tasks = cast(list[dict[str, Any]], data["due_tasks"])
    for t in tasks:
        due = str(t["due_at"] or "")[5:16].replace("T", " ")
        lines.append(f"- {due} {t['title']}" + (" (지남)" if t["overdue"] else ""))
    if not tasks:
        lines.append("- 없음")
    routines = cast(list[dict[str, Any]], data["routines"])
    if routines:
        lines += ["", "## 루틴"]
        lines += [f"- [{'x' if r['done'] else ' '}] {r['title']}" for r in routines]
    lines += ["", f"인박스 {data['open_inbox']}개"]
    return "\n".join(lines)
