"""Agent runs (PLAN Phase 5): who answers a message, and streaming their answers.

Routing (user decision 2026-09-25): plain messages are capture (inbox); agents answer
only when addressed —
  1. @mentions in the message (several → parallel, one → the thread sticks to it)
  2. a reply in a thread that is stuck to an agent
  3. any message in an agent's DM channel
  4. /ask → the channel's default agent, else the app-wide default
Each run streams `agent.token` / `agent.status` over the WebSocket and writes the
finished text to its reply message once, so SQLite sees one write per run.
"""

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from argos import attachments, services
from argos.agents import (
    AgentAdapter,
    AgentUnavailable,
    Failure,
    Status,
    Token,
    Turn,
    build_adapter,
)
from argos.config import Settings
from argos.debate import DebateRunner
from argos.hub import hub
from argos.models import (
    Agent,
    AgentBackend,
    AuthorType,
    Channel,
    ChannelKind,
    Event,
    Message,
    RunStatus,
    Task,
    TaskStatus,
)
from argos.skills import SkillCache, SkillLister, apply_skill, list_skills, parse_call
from argos.usage import UsageMonitor, busy_note

# Agents whose plan usage Argos reads (PLAN Phase 9).
USAGE_PROVIDER = {AgentBackend.CLAUDE_CODE: "claude", AgentBackend.CODEX: "codex"}

log = logging.getLogger(__name__)

MENTION = re.compile(r"(?<![\w@])@([A-Za-z0-9_\-가-힣]+)")
TRANSCRIPT_LIMIT = 30  # messages sent back to an agent
WEEKDAY_KO = "월화수목금토일"


async def mentioned_agents(session: AsyncSession, text: str) -> list[Agent]:
    found: list[Agent] = []
    for handle in MENTION.findall(text):
        agent = await services.find_agent(session, handle)
        if agent is not None and agent not in found:
            found.append(agent)
    return found


async def build_context(
    session: AsyncSession, agent: Agent, channel: Channel, settings: Settings
) -> str:
    """System context: who the agent is, where it is talking, what is open there
    (PLAN Phase 5 "컨텍스트 주입", capped at settings.context_limit characters)."""
    tz: ZoneInfo = settings.zoneinfo
    now = datetime.now(UTC).astimezone(tz)
    if settings.language == "en":
        kind = {
            "course": "course",
            "project": "project",
            "personal": "personal",
            "dm": "direct message",
        }.get(channel.kind, "shared")
        lines = [
            f'You are "{agent.display_name}", an agent in Argos, a personal productivity app.',
            f"Current time: {now:%Y-%m-%d %H:%M} {tz.key}",
            f"Conversation: #{channel.name} ({kind})",
            "Use Argos tools to create or change tasks and events when possible. "
            "Deletion requires user approval.",
            "Reply briefly and clearly in English.",
        ]
    else:
        kind = {"course": "과목", "project": "프로젝트", "personal": "일상", "dm": "1:1 대화"}.get(
            channel.kind, "공용"
        )
        lines = [
            f"너는 개인 일정·학업 관리 앱 Argos 안에서 사용자와 대화하는 에이전트 "
            f'"{agent.display_name}"다.',
            f"현재 시각: {now:%Y-%m-%d} ({WEEKDAY_KO[now.weekday()]}) {now:%H:%M} {tz.key}",
            f"대화 위치: #{channel.name} ({kind})",
            "일정·할 일을 기록하거나 바꿀 때는 가능하면 Argos 도구를 써라. "
            "삭제는 사용자 승인이 필요하다.",
            "한국어로 짧고 분명하게 답한다.",
        ]
    if channel.kind not in (ChannelKind.DM, ChannelKind.SYSTEM):
        tasks = (
            await session.scalars(
                select(Task)
                .where(Task.channel_id == channel.id, Task.status != TaskStatus.DONE)
                .order_by(Task.due_at.is_(None), Task.due_at)
                .limit(10)
            )
        ).all()
        if tasks:
            lines.append(
                "Open tasks in this channel:"
                if settings.language == "en"
                else "이 채널의 열린 할 일:"
            )
            for t in tasks:
                due = ""
                if t.due_at:
                    prefix = "due" if settings.language == "en" else "마감"
                    due = f", {prefix} {t.due_at.astimezone(tz):%m/%d %H:%M}"
                lines.append(f"- {t.title} ({t.status}{due})")
        events = (
            await session.scalars(
                select(Event)
                .where(Event.channel_id == channel.id, Event.starts_at >= datetime.now(UTC))
                .order_by(Event.starts_at)
                .limit(5)
            )
        ).all()
        if events:
            lines.append("Upcoming events:" if settings.language == "en" else "다가오는 일정:")
            lines += [
                f"- {e.title} ({e.starts_at.astimezone(tz):%m/%d %H:%M})"
                for e in events
                if e.starts_at
            ]
    if agent.system_prompt:
        lines.append(agent.system_prompt)
    return "\n".join(lines)[: settings.context_limit]


async def build_transcript(session: AsyncSession, trigger: Message, directory: Path) -> list[Turn]:
    """The thread the trigger is in; in a DM, the recent turns of its conversation.
    `directory`: where attachments are stored (Settings.attachments_dir)."""
    channel = await session.get(Channel, trigger.channel_id)
    if trigger.thread_root_id is not None:
        root, replies = await services.list_thread(session, trigger.thread_root_id)
        messages = [root, *replies]
    elif channel is not None and channel.kind == ChannelKind.DM:
        recent = await session.scalars(
            select(Message)
            .where(Message.channel_id == channel.id, Message.session_id == trigger.session_id)
            .order_by(Message.created_at.desc())
            .limit(TRANSCRIPT_LIMIT)
        )
        messages = list(recent.all())[::-1]
    else:
        messages = [trigger]
    files = await services.attachments_for(session, [m.id for m in messages])
    turns = [
        Turn(
            "user" if m.author_type == "user" else (m.author_id or "system"),
            m.body,
            attachments=tuple(attachments.ref(a, directory) for a in files.get(m.id, [])),
        )
        for m in messages
        if m.body.strip() or m.id in files
    ]
    return turns[-TRANSCRIPT_LIMIT:]


@dataclass
class _Job:
    task_id: str
    workspace: Path
    slot: bool = False  # holds one of the job_concurrency slots


async def _move(session: AsyncSession, task_id: str, status: TaskStatus, agent: Agent) -> None:
    """Card moves made by a job (PLAN Phase 6); `done` is left to the user. A card the
    user deleted meanwhile is skipped."""
    try:
        task = await services.get_task(session, task_id)
        if task.status != status:
            await services.move_task(session, task_id, status=status, actor=f"agent:{agent.name}")
    except services.NotFoundError:
        return


def job_context(
    agent: Agent, channel: Channel, task: Task, workspace: Path, language: str = "ko"
) -> str:
    if language == "en":
        return "\n".join(
            [
                f'You are "{agent.display_name}", a coding agent in Argos.',
                f"Working directory: {workspace}",
                "Do not change files outside this directory. Network access may be restricted.",
                f"Related task: {task.title} (#{channel.name})",
                "In your final response, summarize in English what you did, files changed, "
                "tests and results, and remaining work.",
            ]
        )
    return "\n".join(
        [
            f"너는 개인 학업·프로젝트 관리 앱 Argos에서 코딩 작업(잡)을 맡은 에이전트 "
            f'"{agent.display_name}"다.',
            f"작업 디렉터리: {workspace}",
            "(이 밖의 파일은 바꿀 수 없고, 네트워크는 막혀 있을 수 있다)",
            f"연결된 할 일: {task.title} (#{channel.name})",
            "작업을 마치면 마지막 답변으로 한국어 요약을 남긴다: 무엇을 했는지, 바꾼 파일, "
            "실행한 테스트와 결과, 남은 일.",
        ]
    )


class _AgentSessions:
    """SessionIds backed by the agent_session table."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], agent_id: str) -> None:
        self._sessionmaker = sessionmaker
        self._agent_id = agent_id

    async def get(self, key: str) -> str | None:
        async with self._sessionmaker() as session:
            return await services.get_agent_session(session, self._agent_id, key)

    async def set(self, key: str, value: str) -> None:
        async with self._sessionmaker() as session:
            await services.set_agent_session(session, self._agent_id, key, value)


class Runner:
    """Owns the asyncio task of every live run so a run can be cancelled by id."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Settings,
        adapter_factory: Callable[..., AgentAdapter] = build_adapter,
    ) -> None:
        self._sessionmaker = sessionmaker
        self.settings = settings
        self.adapter_factory = adapter_factory  # tests swap in a fake (PLAN §8.6)
        self.skill_lister: SkillLister = SkillCache(list_skills)  # tests swap this too
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._job_slots = asyncio.Semaphore(max(1, settings.job_concurrency))
        self.usage: UsageMonitor | None = None  # set by the app (PLAN Phase 9)

    @property
    def sessionmaker(self) -> async_sessionmaker[AsyncSession]:
        return self._sessionmaker

    def start_debate(self, debate_id: str) -> None:
        """Runs a debate (PLAN Phase 10) in the background; cancel with its id."""
        task = asyncio.create_task(DebateRunner(self, debate_id).run())
        key = f"debate-{debate_id}"
        self._tasks[key] = task
        task.add_done_callback(lambda _t: self._tasks.pop(key, None))

    async def run_turn(
        self,
        run_id: str,
        agent: Agent,
        message_id: str,
        transcript: list[Turn],
        context: str,
        session_key: str,
        *,
        no_tools: bool = False,
    ) -> None:
        """One agent answer, awaited (debate turns run one after another)."""
        await self._run(
            run_id, agent, message_id, transcript, context, session_key, no_tools=no_tools
        )

    async def warn_if_busy(
        self, session: AsyncSession, agent: Agent, channel_id: str, thread_root_id: str | None
    ) -> None:
        """Routing hint (PLAN Phase 9): when the agent's 5-hour window is ≥ 90% used,
        say so in the thread and name the others. The run still starts."""
        provider = USAGE_PROVIDER.get(agent.backend)
        if self.usage is None or provider is None:
            return
        others = [
            a.name
            for a in await services.list_agents(session)
            if a.id != agent.id
            and (a.backend in USAGE_PROVIDER or a.backend == AgentBackend.HERMES)
        ]
        note = busy_note(
            self.usage.current[provider], agent.display_name, others, datetime.now(UTC)
        )
        if note is not None:
            await services.create_message(
                session,
                channel_id=channel_id,
                body=note,
                author_type=AuthorType.SYSTEM,
                thread_root_id=thread_root_id,
                actor="system",
            )

    def running(self) -> list[str]:
        return list(self._tasks)

    async def respond(
        self, session: AsyncSession, trigger: Message, agents: list[Agent]
    ) -> list[str]:
        """Starts one run per agent; returns run ids. Replies go into the trigger's
        thread, except in a DM where the conversation stays top-level."""
        channel = await services.get_channel(session, trigger.channel_id)
        in_dm = channel.kind == ChannelKind.DM and trigger.thread_root_id is None
        reply_root = None if in_dm else (trigger.thread_root_id or trigger.id)
        root = (
            await services.get_message(session, trigger.thread_root_id)
            if trigger.thread_root_id
            else trigger
        )
        coding = (
            services.coding_workspace(self.settings.job_roots, channel.workspace_path)
            if root.coding and channel.workspace_path
            else None
        )
        run_ids: list[str] = []
        for agent in agents:
            await self.warn_if_busy(session, agent, channel.id, reply_root)
            run, reply = await services.start_run(
                session,
                agent=agent,
                channel_id=channel.id,
                trigger=trigger,
                reply_thread_root_id=reply_root,
                actor="user",
            )
            context = await build_context(session, agent, channel, self.settings)
            transcript = await build_transcript(session, trigger, self.settings.attachments_dir)
            if transcript and parse_call(transcript[-1].text) is not None:
                transcript = apply_skill(transcript, await self.skill_lister(agent, self.settings))
            # One backend conversation per Argos thread (or per DM conversation), like
            # Hermes keeps one session per Discord thread.
            key = (
                f"argos-dm-{trigger.session_id or channel.id}"
                if in_dm
                else f"argos-thread-{run.thread_root_id}"
            )
            task = asyncio.create_task(
                self._run(run.id, agent, reply.id, transcript, context, key, coding=coding)
            )
            self._tasks[run.id] = task
            task.add_done_callback(lambda _t, rid=run.id: self._tasks.pop(rid, None))
            run_ids.append(run.id)
        return run_ids

    async def start_job(
        self,
        session: AsyncSession,
        trigger: Message,
        agent: Agent,
        task: Task,
        instructions: str,
        workspace: Path,
    ) -> str:
        """A coding job (PLAN Phase 6): runs in `workspace` (already checked against the
        allowlist), answers in the trigger's thread, and moves `task` along the board
        (in_progress → review)."""
        channel = await services.get_channel(session, trigger.channel_id)
        await self.warn_if_busy(session, agent, channel.id, trigger.thread_root_id or trigger.id)
        run, reply = await services.start_run(
            session,
            agent=agent,
            channel_id=channel.id,
            trigger=trigger,
            reply_thread_root_id=trigger.thread_root_id or trigger.id,
            actor="user",
            job=(task, instructions, workspace),
        )
        context = job_context(agent, channel, task, workspace, self.settings.language)
        files = await services.attachments_for(session, [trigger.id])
        refs = tuple(
            attachments.ref(a, self.settings.attachments_dir) for a in files.get(trigger.id, [])
        )
        job = _Job(task_id=task.id, workspace=workspace)
        coro = self._run(
            run.id,
            agent,
            reply.id,
            [Turn("user", instructions, attachments=refs)],
            context,
            f"job-{run.id}",
            job,
        )
        running = asyncio.create_task(coro)
        self._tasks[run.id] = running
        running.add_done_callback(lambda _t, rid=run.id: self._tasks.pop(rid, None))
        return run.id

    async def _job_started(self, run_id: str, agent: Agent, job: _Job) -> None:
        async with self._sessionmaker() as session:
            await services.mark_run_started(session, run_id)
            await _move(session, job.task_id, TaskStatus.IN_PROGRESS, agent)

    def cancel(self, run_id: str) -> bool:
        task = self._tasks.get(run_id)
        if task is None:
            return False
        task.cancel()
        return True

    async def shutdown(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    async def _run(
        self,
        run_id: str,
        agent: Agent,
        message_id: str,
        transcript: list[Turn],
        context: str,
        session_key: str,
        job: "_Job | None" = None,
        no_tools: bool = False,
        coding: Path | None = None,
    ) -> None:
        """`coding`: the project folder of a coding-mode thread."""
        ids = {"run_id": run_id, "agent_id": agent.name, "message_id": message_id}
        parts: list[str] = []
        trace: list[str] = []  # job log: one line per tool step
        status, error = RunStatus.DONE, None
        try:
            if job is not None:
                await hub.publish("agent.status", ids | {"status": "대기 중"})
                await self._job_slots.acquire()
                job.slot = True
                await self._job_started(run_id, agent, job)
            await hub.publish("agent.status", ids | {"status": "thinking"})
            sessions = _AgentSessions(self._sessionmaker, agent.id)
            if job is not None:
                adapter = self.adapter_factory(
                    agent, self.settings, sessions, job_workspace=job.workspace
                )
            elif coding is not None:
                adapter = self.adapter_factory(
                    agent, self.settings, sessions, job_workspace=coding, coding=True
                )
            elif no_tools:
                adapter = self.adapter_factory(agent, self.settings, sessions, no_tools=True)
            else:
                adapter = self.adapter_factory(agent, self.settings, sessions)
            working = job is not None or coding is not None
            limit = self.settings.job_timeout if working else self.settings.agent_timeout
            async with asyncio.timeout(limit):
                async for event in adapter.stream(transcript, context, session_key):
                    match event:
                        case Token(text=text):
                            parts.append(text)
                            await hub.publish("agent.token", ids | {"text": text})
                        case Status(text=text):
                            now = datetime.now(self.settings.zoneinfo)
                            trace.append(f"{now:%H:%M:%S} {text}")
                            await hub.publish("agent.status", ids | {"status": text})
                        case Failure(message=message):
                            raise AgentUnavailable(message)
        except asyncio.CancelledError:
            status = RunStatus.CANCELLED
        except TimeoutError:
            status, error = RunStatus.ERROR, "응답 시간이 초과됐어요"
        except AgentUnavailable as exc:
            status, error = RunStatus.ERROR, str(exc)
        except Exception as exc:  # an adapter bug must not take the server down
            log.exception("agent run %s failed", run_id)
            status, error = RunStatus.ERROR, f"{type(exc).__name__}: {exc}"
        finally:
            if job is not None and job.slot:
                self._job_slots.release()
        text = "".join(parts).strip()
        async with self._sessionmaker() as session:
            await services.finish_run(
                session,
                run_id,
                text=text,
                status=status,
                error=error,
                log="\n".join(trace) if job is not None or coding is not None else None,
            )
            if job is not None and status == RunStatus.DONE:
                await _move(session, job.task_id, TaskStatus.REVIEW, agent)
        if status == RunStatus.ERROR:
            await hub.publish("agent.error", ids | {"error": error})
        else:
            await hub.publish("agent.done", ids | {"status": status})
