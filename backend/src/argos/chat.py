"""Chat input (PLAN Phase 3): sending a message is how things get recorded.

Slash commands act immediately and deterministically. Anything else is kept verbatim as
an inbox item first, so nothing is lost if classification fails, and is then classified
in the background.
"""

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from argos import commands, services
from argos.classifier import Classifier, ClassifierError, ClassifyContext, anchor_dates
from argos.config import Settings
from argos.models import (
    AuthorType,
    Channel,
    ChannelKind,
    InboxItem,
    InboxStatus,
    Message,
    Record,
)

ASK_PENDING = "에이전트와의 대화는 아직 연결 전이에요. 질문은 이 스레드에 남겨 둘게요."


async def post_message(
    session: AsyncSession,
    *,
    channel_id: str,
    body: str,
    now: datetime,
    settings: Settings,
    thread_root_id: str | None = None,
) -> tuple[Message, str | None]:
    """Returns the stored message and, for plain text, the inbox item to classify."""
    body = body.strip()
    if not body:
        raise services.InvalidError("빈 메시지는 보낼 수 없어요")
    channel = await services.get_channel(session, channel_id)

    if thread_root_id is not None:  # replies are conversation, not capture
        message = await services.create_message(
            session, channel_id=channel.id, body=body, actor="user", thread_root_id=thread_root_id
        )
        return message, None

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
            message = await _say(session, channel, body, item)
            return message, item.id
        case commands.TaskCommand(title=title, due_at=due_at):
            task = await services.create_task(
                session, channel_id=channel.id, title=title, due_at=due_at, actor="user"
            )
            return await _say(session, channel, body, task), None
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
            return await _say(session, channel, body, event), None
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
            return await _say(session, channel, body, item), None
        case commands.AskCommand():
            message = await _say(session, channel, body, None)
            await services.create_message(
                session,
                channel_id=channel.id,
                body=ASK_PENDING,
                author_type=AuthorType.SYSTEM,
                thread_root_id=message.id,
                actor="system",
            )
            return message, None


async def _say(session: AsyncSession, channel: Channel, body: str, ref: Record | None) -> Message:
    return await services.create_message(
        session, channel_id=channel.id, body=body, actor="user", ref=ref
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
            select(Channel.name).where(Channel.kind != ChannelKind.SYSTEM).order_by(Channel.name)
        )
        context = ClassifyContext(
            now=now,
            tz=settings.zoneinfo,
            channel_name=channel.name if channel else None,
            channel_kind=channel.kind if channel else None,
            channel_names=list(names.all()),
            personal_channel=personal.name if personal else None,
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
