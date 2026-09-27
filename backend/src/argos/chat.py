"""Chat input (PLAN Phases 3 and 5): sending a message is how things get recorded, and
how agents are called.

Slash commands act immediately and deterministically. Anything else is kept verbatim as
an inbox item first, so nothing is lost if classification fails, and is then classified
in the background.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from argos import commands, services
from argos.classifier import Classifier, ClassifierError, ClassifyContext, anchor_dates
from argos.config import Settings
from argos.models import (
    Agent,
    AgentBackend,
    AuthorType,
    Channel,
    ChannelKind,
    Debate,
    InboxItem,
    InboxStatus,
    Message,
    Record,
    Task,
)
from argos.runner import mentioned_agents

NO_AGENT = "답할 에이전트를 찾지 못했어요. 설정에서 기본 에이전트를 확인해 주세요."


def job_title(instructions: str) -> str:
    """Card title: the first line, cut at a word near 40 characters."""
    line = instructions.strip().splitlines()[0]
    if len(line) <= 40:
        return line
    cut = line[:40].rsplit(" ", 1)[0]
    return (cut if len(cut) >= 20 else line[:40]) + "…"


@dataclass
class JobRequest:
    agent: Agent
    task: Task
    instructions: str
    workspace: Path


@dataclass
class Posted:
    message: Message
    classify_item_id: str | None = None  # plain text captured into the inbox
    agents: list[Agent] = field(default_factory=list[Agent])  # who should answer
    job: JobRequest | None = None  # /job: a coding job for the runner
    debate: Debate | None = None  # /debate: turns for the debate runner


async def post_message(
    session: AsyncSession,
    *,
    channel_id: str,
    body: str,
    now: datetime,
    settings: Settings,
    thread_root_id: str | None = None,
) -> Posted:
    """Stores the message and decides what happens next (see runner.py for routing):
    agents to answer, or an inbox item to classify, or neither."""
    body = body.strip()
    if not body:
        raise services.InvalidError("빈 메시지는 보낼 수 없어요")
    channel = await services.get_channel(session, channel_id)
    personal = (
        await services.get_personal_channel(session)
        if channel.kind == ChannelKind.SYSTEM and channel.name == "inbox"
        else None
    )
    agent_channel = personal or channel
    mentioned = await mentioned_agents(session, body)
    dm_agent = (
        await services.get_agent(session, channel.default_agent_id)
        if channel.kind == ChannelKind.DM and channel.default_agent_id
        else None
    )

    root: Message | None = None
    if thread_root_id is not None:
        root = await services.get_message(session, thread_root_id)
        root = await services.get_message(session, root.thread_root_id or root.id)

    # A reply is conversation, not capture; slash commands work in threads too (their
    # cards and agent answers stay in the thread).
    if root is not None and not body.startswith("/"):
        message = await services.create_message(
            session, channel_id=channel.id, body=body, actor="user", thread_root_id=root.id
        )
        if root.ref_type == "debate" or await services.running_debate(session, root.id):
            return Posted(message)  # the debate reads it at the next turn
        if len(mentioned) == 1:  # an @mention re-sticks the thread to that agent
            await services.update_message(
                session, root.id, {"sticky_agent_id": mentioned[0].id}, "user"
            )
        if mentioned:
            return Posted(message, agents=mentioned)
        if root.sticky_agent_id:
            return Posted(message, agents=[await services.get_agent(session, root.sticky_agent_id)])
        return Posted(message, agents=[dm_agent] if dm_agent else [])
    in_thread = root.id if root is not None else None

    # /job and /debate name agents with @ too, but they are commands, not conversations.
    names_agents = body.lstrip().startswith(("/job", "/debate"))
    if (mentioned or dm_agent) and not names_agents and root is None:  # a conversation
        targets = mentioned or ([dm_agent] if dm_agent else [])
        message = await _say(session, channel, body, None)
        if len(targets) == 1:
            await services.update_message(
                session, message.id, {"sticky_agent_id": targets[0].id}, "user"
            )
        return Posted(message, agents=targets)

    try:
        command = commands.parse(body, now, settings.zoneinfo)
    except commands.CommandError as exc:
        raise services.InvalidError(str(exc)) from exc

    match command:
        case None:
            item = await services.create_inbox_item(
                session,
                raw_text=body,
                captured_via=f"chat:#{channel.name}",
                channel_id=channel.id,
                actor="user",
            )
            return Posted(
                await _say(session, channel, body, item, in_thread), classify_item_id=item.id
            )
        case commands.TaskCommand(title=title, due_at=due_at):
            task = await services.create_task(
                session, channel_id=channel.id, title=title, due_at=due_at, actor="user"
            )
            return Posted(await _say(session, channel, body, task, in_thread))
        case commands.EventCommand() as event_cmd:
            event = await services.create_event(
                session,
                channel_id=channel.id,
                title=event_cmd.title,
                starts_at=event_cmd.starts_at,
                ends_at=event_cmd.ends_at,
                start_date=event_cmd.all_day,
                actor="user",
            )
            return Posted(await _say(session, channel, body, event, in_thread))
        case commands.NoteCommand(text=text):
            # Becomes a Markdown file once the vault is connected (Phase 8); kept until then.
            item = await services.create_inbox_item(
                session,
                raw_text=text,
                captured_via="/note",
                channel_id=channel.id,
                actor="user",
                suggestion={
                    "type": "study_note",
                    "title": text[:60],
                    "summary": text,
                    "channel_hint": None if channel.kind == ChannelKind.SYSTEM else channel.name,
                    "confidence": 1.0,
                },
            )
            return Posted(await _say(session, channel, body, item, in_thread))
        case commands.JobCommand(agent=handle, instructions=instructions, directory=directory):
            agent = await services.find_agent(session, handle)
            if agent is None or agent.backend not in (
                AgentBackend.CLAUDE_CODE,
                AgentBackend.CODEX,
            ):
                raise services.InvalidError("코딩 잡은 @claude나 @codex에게만 맡길 수 있어요")
            title = job_title(instructions)
            # Validate the directory before anything is stored (PLAN §8.1 allowlist).
            workspace = services.job_workspace(settings.job_roots, directory, title)
            task = await services.create_task(
                session, channel_id=channel.id, title=title, description=instructions, actor="user"
            )
            message = await _say(session, channel, body, task, in_thread)
            return Posted(message, job=JobRequest(agent, task, instructions, workspace))
        case commands.DebateCommand() as debate_cmd:
            found = [await services.find_agent(session, h) for h in debate_cmd.agents]
            missing = [f"@{h}" for h, a in zip(debate_cmd.agents, found, strict=True) if a is None]
            if missing:
                raise services.InvalidError(f"없는 에이전트예요: {', '.join(missing)}")
            participants = [a for a in found if a is not None]
            moderator = (
                await services.default_agent(session, agent_channel, settings.default_agent)
                or participants[0]
            )
            debate = await services.create_debate(
                session,
                channel_id=channel.id,
                topic=debate_cmd.topic,
                mode=debate_cmd.mode,
                participants=[a.name for a in participants],
                moderator=moderator.name,
                max_rounds=debate_cmd.rounds,
                use_tools=debate_cmd.tools,
                actor="user",
            )
            message = await _say(session, channel, body, debate, in_thread)
            debate = await services.update_debate(
                session, debate.id, {"thread_root_id": in_thread or message.id}, "system"
            )
            return Posted(message, debate=debate)
        case commands.AskCommand():
            message = await _say(session, channel, body, None, in_thread)
            agent = await services.default_agent(session, agent_channel, settings.default_agent)
            if agent is None:
                await services.create_message(
                    session,
                    channel_id=channel.id,
                    body=NO_AGENT,
                    author_type=AuthorType.SYSTEM,
                    thread_root_id=in_thread or message.id,
                    actor="system",
                )
                return Posted(message)
            await services.update_message(
                session, in_thread or message.id, {"sticky_agent_id": agent.id}, "user"
            )
            return Posted(message, agents=[agent])


async def _say(
    session: AsyncSession,
    channel: Channel,
    body: str,
    ref: Record | None,
    thread_root_id: str | None = None,
) -> Message:
    return await services.create_message(
        session,
        channel_id=channel.id,
        body=body,
        actor="user",
        ref=ref,
        thread_root_id=thread_root_id,
    )


async def classify_item(
    sessionmaker: async_sessionmaker[AsyncSession],
    classifier: Classifier,
    item_id: str,
    settings: Settings,
    now: datetime,
) -> None:
    """Background step: fill the suggestion, or record why it failed. The raw text stays
    in the inbox either way. Auto-applies only listed types above the threshold."""
    async with sessionmaker() as session:
        item = await session.get(InboxItem, item_id)
        if item is None or item.status != InboxStatus.NEW:
            return  # deleted or handled while we waited
        channel = await session.get(Channel, item.channel_id) if item.channel_id else None
        personal = await services.get_personal_channel(session)
        names = await session.scalars(
            select(Channel.name)
            .where(Channel.kind.not_in([ChannelKind.SYSTEM, ChannelKind.DM]))
            .order_by(Channel.name)
        )
        context = ClassifyContext(
            now=now,
            tz=settings.zoneinfo,
            channel_name=channel.name if channel else None,
            channel_kind=channel.kind if channel else None,
            channel_names=list(names.all()),
            personal_channel=personal.name if personal else None,
            language=settings.language,
        )
        try:
            suggestion = await classifier.classify(item.raw_text, context)
            suggestion = anchor_dates(suggestion, item.raw_text, context)
            if suggestion.channel_hint not in context.channel_names:
                suggestion = suggestion.model_copy(update={"channel_hint": None})
        except ClassifierError as exc:
            if not await _still_new(session, item):
                return
            logging.getLogger(__name__).warning("classification failed for %s: %s", item_id, exc)
            await services.update_inbox_item(
                session, item_id, {"suggestion_json": {"error": str(exc)}}, "classifier"
            )
            return

        if not await _still_new(session, item):
            return
        await services.update_inbox_item(
            session,
            item_id,
            {
                "suggestion_json": suggestion.model_dump(mode="json"),
                "confidence": suggestion.confidence,
                "status": InboxStatus.SUGGESTED,
            },
            "classifier",
        )
        if (
            suggestion.type in settings.classifier_auto_apply
            and suggestion.confidence >= settings.classifier_threshold
        ):
            try:
                await services.accept_inbox_item(session, item_id, actor="classifier")
            except services.InvalidError:
                pass  # e.g. no channel could be resolved: leave it for the user to confirm


async def _still_new(session: AsyncSession, item: InboxItem) -> bool:
    """The model call takes seconds; the user may have accepted, converted or dismissed
    the item meanwhile, and a late result must not reopen it."""
    await session.refresh(item)
    return item.status == InboxStatus.NEW
