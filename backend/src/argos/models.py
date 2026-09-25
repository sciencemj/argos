import uuid
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Dialect,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from argos.db import Base


class UTCDateTime(TypeDecorator[datetime]):
    """Stores aware datetimes as naive UTC (SQLite has no tz) and returns them aware."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime; pass a timezone-aware value")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return value.replace(tzinfo=UTC) if value is not None else None


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid.uuid4())


class ChannelKind(StrEnum):
    COURSE = "course"
    PROJECT = "project"
    # The single built-in #일상 channel for everyday things outside courses/projects.
    PERSONAL = "personal"
    DM = "dm"  # one-to-one conversation with an agent
    SYSTEM = "system"


class TaskStatus(StrEnum):
    BACKLOG = "backlog"
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    DONE = "done"


class InboxStatus(StrEnum):
    NEW = "new"
    SUGGESTED = "suggested"
    ACCEPTED = "accepted"
    DISMISSED = "dismissed"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    FAILED = "failed"  # approved, but running the action failed


class AuthorType(StrEnum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class Record(Base):
    __abstract__ = True

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Area(Record):
    __tablename__ = "area"

    name: Mapped[str] = mapped_column(String(100), unique=True)
    icon: Mapped[str | None] = mapped_column(String(20))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Channel(Record):
    __tablename__ = "channel"

    area_id: Mapped[str | None] = mapped_column(ForeignKey("area.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(100), unique=True)
    kind: Mapped[ChannelKind] = mapped_column(String(20))
    default_agent_id: Mapped[str | None] = mapped_column(String(36))
    vault_path: Mapped[str | None] = mapped_column(String(500))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Task(Record):
    __tablename__ = "task"

    channel_id: Mapped[str] = mapped_column(
        ForeignKey("channel.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[TaskStatus] = mapped_column(String(20), default=TaskStatus.TODO)
    position: Mapped[float] = mapped_column(Float)
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    priority: Mapped[int | None] = mapped_column(Integer)


class Event(Record):
    """Timed events use starts_at/ends_at; all-day events use start_date/end_date
    (end exclusive, like iCalendar) so no timezone shift can move them a day."""

    __tablename__ = "event"

    channel_id: Mapped[str] = mapped_column(
        ForeignKey("channel.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(500))
    starts_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    ends_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    start_date: Mapped[date | None] = mapped_column(Date, index=True)
    end_date: Mapped[date | None] = mapped_column(Date)
    location: Mapped[str | None] = mapped_column(String(500))
    rrule: Mapped[str | None] = mapped_column(String(500))
    calendar_id: Mapped[str | None] = mapped_column(String(200))

    @property
    def all_day(self) -> bool:
        return self.start_date is not None


class InboxItem(Record):
    __tablename__ = "inbox_item"

    # Channel the text was typed in (classification context); None for outside capture.
    channel_id: Mapped[str | None] = mapped_column(
        ForeignKey("channel.id", ondelete="SET NULL"), index=True
    )
    raw_text: Mapped[str] = mapped_column(Text)
    captured_via: Mapped[str] = mapped_column(String(50))
    status: Mapped[InboxStatus] = mapped_column(String(20), default=InboxStatus.NEW, index=True)
    suggestion_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    confidence: Mapped[float | None] = mapped_column(Float)


class Message(Record):
    __tablename__ = "message"

    channel_id: Mapped[str] = mapped_column(
        ForeignKey("channel.id", ondelete="CASCADE"), index=True
    )
    thread_root_id: Mapped[str | None] = mapped_column(ForeignKey("message.id"), index=True)
    author_type: Mapped[AuthorType] = mapped_column(String(20))
    author_id: Mapped[str | None] = mapped_column(String(36))
    body: Mapped[str] = mapped_column(Text)
    ref_type: Mapped[str | None] = mapped_column(String(50))
    ref_id: Mapped[str | None] = mapped_column(String(36))
    run_id: Mapped[str | None] = mapped_column(String(36))
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # On a thread root: the agent the thread is stuck to after an @mention (PLAN Phase 5).
    sticky_agent_id: Mapped[str | None] = mapped_column(String(36))


class ActivityLog(Record):
    __tablename__ = "activity_log"

    object_type: Mapped[str] = mapped_column(String(50))
    object_id: Mapped[str] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(50))
    before_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    actor: Mapped[str] = mapped_column(String(100))


class SourceLink(Record):
    """Where an Argos object came from or is mirrored to outside (PLAN §5, Phase 7b):
    one CalDAV resource per event. Change detection compares the remote ETag and the
    object's updated_at with what they were at the last sync."""

    __tablename__ = "source_link"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_source_link_external"),)

    object_type: Mapped[str] = mapped_column(String(20))
    object_id: Mapped[str] = mapped_column(String(36), index=True)
    source: Mapped[str] = mapped_column(String(20))  # "icloud"
    external_id: Mapped[str] = mapped_column(String(1000))  # resource href
    calendar_url: Mapped[str] = mapped_column(String(1000))
    calendar_name: Mapped[str] = mapped_column(String(200))
    uid: Mapped[str] = mapped_column(String(500))
    etag: Mapped[str | None] = mapped_column(String(200))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    # Only the Argos calendar is written to; everything else is shown, not edited.
    read_only: Mapped[bool] = mapped_column(default=True)
    local_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Remote fields waiting for the user when both sides changed since the last sync.
    conflict: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class AppSetting(Record):
    """User-changeable settings that override .env (e.g. the classifier model)."""

    __tablename__ = "app_setting"

    key: Mapped[str] = mapped_column(String(100), unique=True)
    value: Mapped[Any] = mapped_column(JSON)


class Routine(Record):
    """A daily checklist item (데일리 루틴), not a kanban task."""

    __tablename__ = "routine"

    title: Mapped[str] = mapped_column(String(200))
    # Days it repeats, Monday=0 … Sunday=6 as digits: "0123456" every day, "01234" weekdays.
    weekdays: Mapped[str] = mapped_column(String(7), default="0123456")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class RoutineCheck(Record):
    """One routine done on one local calendar day."""

    __tablename__ = "routine_check"
    __table_args__ = (UniqueConstraint("routine_id", "day", name="uq_routine_check_day"),)

    routine_id: Mapped[str] = mapped_column(
        ForeignKey("routine.id", ondelete="CASCADE"), index=True
    )
    day: Mapped[date] = mapped_column(Date)


class Approval(Record):
    """A destructive action an agent asked for, waiting for the user (PLAN P5)."""

    __tablename__ = "approval"

    requested_by: Mapped[str] = mapped_column(String(100))  # actor, e.g. "agent:claude"
    run_id: Mapped[str | None] = mapped_column(String(36))  # agent_run from Phase 5
    channel_id: Mapped[str | None] = mapped_column(
        ForeignKey("channel.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str] = mapped_column(String(50))  # e.g. "delete_task"
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    summary: Mapped[str] = mapped_column(String(500))  # what the card shows
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ApprovalStatus] = mapped_column(
        String(20), default=ApprovalStatus.PENDING, index=True
    )
    error: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class AgentBackend(StrEnum):
    HERMES = "hermes"  # Hermes gateway, OpenAI-compatible API
    OLLAMA = "ollama"  # local model through Ollama's OpenAI-compatible API
    CLAUDE_CODE = "claude_code"  # `claude -p` headless
    CODEX = "codex"  # `codex exec --json`


class RunStatus(StrEnum):
    QUEUED = "queued"  # a job waiting for a free slot (settings.job_concurrency)
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


class Agent(Record):
    __tablename__ = "agent"

    name: Mapped[str] = mapped_column(String(40), unique=True)  # @mention handle
    display_name: Mapped[str] = mapped_column(String(100))
    avatar: Mapped[str | None] = mapped_column(String(10))
    backend: Mapped[AgentBackend] = mapped_column(String(20))
    model: Mapped[str | None] = mapped_column(String(200))  # None = the backend's default
    system_prompt: Mapped[str | None] = mapped_column(Text)
    tools_json: Mapped[list[str] | None] = mapped_column(JSON)  # Phase 10 whitelist
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)


class AgentRun(Record):
    """One agent answering one message; the reply message fills in as it streams."""

    __tablename__ = "agent_run"

    agent_id: Mapped[str] = mapped_column(ForeignKey("agent.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channel.id", ondelete="CASCADE"))
    thread_root_id: Mapped[str | None] = mapped_column(String(36), index=True)
    trigger_message_id: Mapped[str | None] = mapped_column(String(36), index=True)
    reply_message_id: Mapped[str | None] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(20), default="chat")  # chat | job | debate
    status: Mapped[RunStatus] = mapped_column(String(20), default=RunStatus.RUNNING)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    error: Mapped[str | None] = mapped_column(Text)
    task_id: Mapped[str | None] = mapped_column(String(36), index=True)  # jobs: the card
    # Jobs (PLAN Phase 6): what was asked, where the agent may write, what it did.
    instructions: Mapped[str | None] = mapped_column(Text)
    workspace: Mapped[str | None] = mapped_column(String(500))
    log: Mapped[str | None] = mapped_column(Text)  # tool-use trace, one line per step


class AgentSession(Record):
    """A backend's own conversation id for one Argos thread/DM (e.g. a Codex thread id),
    for backends that pick their ids themselves."""

    __tablename__ = "agent_session"
    __table_args__ = (UniqueConstraint("agent_id", "session_key", name="uq_agent_session_key"),)

    agent_id: Mapped[str] = mapped_column(ForeignKey("agent.id", ondelete="CASCADE"))
    session_key: Mapped[str] = mapped_column(String(100))
    external_id: Mapped[str] = mapped_column(String(200))
