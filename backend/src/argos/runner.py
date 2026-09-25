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
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from argos import services
from argos.agents import (
    AgentAdapter,
    AgentUnavailable,
    Failure,
    SessionIds,
    Status,
    Token,
    Turn,
    build_adapter,
)
from argos.config import Settings
from argos.hub import hub
from argos.models import Agent, Channel, ChannelKind, Event, Message, RunStatus, Task, TaskStatus

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
            lines.append("이 채널의 열린 할 일:")
            for t in tasks:
                due = f", 마감 {t.due_at.astimezone(tz):%m/%d %H:%M}" if t.due_at else ""
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
            lines.append("다가오는 일정:")
            lines += [
                f"- {e.title} ({e.starts_at.astimezone(tz):%m/%d %H:%M})"
                for e in events
                if e.starts_at
            ]
    if agent.system_prompt:
        lines.append(agent.system_prompt)
    return "\n".join(lines)[: settings.context_limit]


async def build_transcript(session: AsyncSession, trigger: Message) -> list[Turn]:
    """The thread the trigger is in; in a DM, the recent conversation."""
    channel = await session.get(Channel, trigger.channel_id)
    if trigger.thread_root_id is not None:
        root, replies = await services.list_thread(session, trigger.thread_root_id)
        messages = [root, *replies]
    elif channel is not None and channel.kind == ChannelKind.DM:
        recent = await session.scalars(
            select(Message)
            .where(Message.channel_id == channel.id)
            .order_by(Message.created_at.desc())
            .limit(TRANSCRIPT_LIMIT)
        )
        messages = list(recent.all())[::-1]
    else:
        messages = [trigger]
    turns = [
        Turn("user" if m.author_type == "user" else (m.author_id or "system"), m.body)
        for m in messages
        if m.body.strip()
    ]
    return turns[-TRANSCRIPT_LIMIT:]


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
        adapter_factory: Callable[[Agent, Settings, SessionIds], AgentAdapter] = build_adapter,
    ) -> None:
        self._sessionmaker = sessionmaker
        self.settings = settings
        self.adapter_factory = adapter_factory  # tests swap in a fake (PLAN §8.6)
        self._tasks: dict[str, asyncio.Task[None]] = {}

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
        run_ids: list[str] = []
        for agent in agents:
            run, reply = await services.start_run(
                session,
                agent=agent,
                channel_id=channel.id,
                trigger=trigger,
                reply_thread_root_id=reply_root,
                actor="user",
            )
            context = await build_context(session, agent, channel, self.settings)
            transcript = await build_transcript(session, trigger)
            # One backend conversation per Argos thread (or per DM), like Hermes keeps one
            # session per Discord thread.
            key = f"argos-dm-{channel.id}" if in_dm else f"argos-thread-{run.thread_root_id}"
            task = asyncio.create_task(self._run(run.id, agent, reply.id, transcript, context, key))
            self._tasks[run.id] = task
            task.add_done_callback(lambda _t, rid=run.id: self._tasks.pop(rid, None))
            run_ids.append(run.id)
        return run_ids

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
    ) -> None:
        ids = {"run_id": run_id, "agent_id": agent.name, "message_id": message_id}
        await hub.publish("agent.status", ids | {"status": "thinking"})
        parts: list[str] = []
        status, error = RunStatus.DONE, None
        try:
            adapter = self.adapter_factory(
                agent, self.settings, _AgentSessions(self._sessionmaker, agent.id)
            )
            async with asyncio.timeout(self.settings.agent_timeout):
                async for event in adapter.stream(transcript, context, session_key):
                    match event:
                        case Token(text=text):
                            parts.append(text)
                            await hub.publish("agent.token", ids | {"text": text})
                        case Status(text=text):
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
        text = "".join(parts).strip()
        async with self._sessionmaker() as session:
            await services.finish_run(session, run_id, text=text, status=status, error=error)
        if status == RunStatus.ERROR:
            await hub.publish("agent.error", ids | {"error": error})
        else:
            await hub.publish("agent.done", ids | {"status": status})
