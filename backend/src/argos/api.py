from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    FastAPI,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException

from argos import chat, classifier, services
from argos.classifier import Classifier
from argos.config import Settings
from argos.db import session_scope
from argos.hub import hub
from argos.models import (
    Approval,
    ApprovalStatus,
    AuthorType,
    ChannelKind,
    Event,
    InboxItem,
    InboxStatus,
    Message,
    Task,
    TaskStatus,
)

USER = "user"


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async for session in session_scope(request.app.state.sessionmaker):
        yield session


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


Session = Annotated[AsyncSession, Depends(get_session)]
Config = Annotated[Settings, Depends(get_settings)]

router = APIRouter(prefix="/api/v1")


# --- schemas ------------------------------------------------------------------


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @field_validator("*", mode="after")
    @classmethod
    def _utc(cls, value: Any) -> Any:
        # Stored times are UTC (PLAN §5); echo them back as UTC whatever the input offset.
        return value.astimezone(UTC) if isinstance(value, datetime) else value


class AreaOut(Out):
    id: str
    name: str
    icon: str | None
    sort_order: int


class ChannelOut(Out):
    id: str
    area_id: str | None
    name: str
    kind: ChannelKind
    vault_path: str | None
    sort_order: int


class AreaCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    icon: str | None = Field(default=None, max_length=20)


class AreaUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    icon: str | None = Field(default=None, max_length=20)
    sort_order: int | None = None


class ChannelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    area_id: str
    kind: ChannelKind = ChannelKind.COURSE
    vault_path: str | None = None


class ChannelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    area_id: str | None = None
    kind: ChannelKind | None = None
    vault_path: str | None = None
    sort_order: int | None = None


class RoutineOut(BaseModel):
    id: str
    title: str
    weekdays: str
    sort_order: int
    scheduled: bool  # repeats on the requested day
    done: bool  # checked on the requested day
    streak: int  # consecutive scheduled days done, as of today


class RoutinesOut(BaseModel):
    day: date
    today: date
    routines: list[RoutineOut]


class RoutineCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    weekdays: str = Field(default="0123456", min_length=1, max_length=7)


class RoutineUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    weekdays: str | None = Field(default=None, min_length=1, max_length=7)
    sort_order: int | None = None


class RoutineCheckIn(BaseModel):
    done: bool


class ClassifierSettingsOut(BaseModel):
    provider: Literal["ollama", "hermes"]
    model: str
    # "app": picked in the settings screen, "env": from .env, "none": classification off
    source: Literal["app", "env", "none"]
    enabled: bool


class ClassifierSettingsIn(BaseModel):
    model: str | None = Field(default=None, max_length=200)  # null or "" turns it off


class OllamaModelOut(BaseModel):
    name: str
    remote: bool
    parameter_size: str | None


class OllamaModelsOut(BaseModel):
    reachable: bool
    error: str | None = None
    models: list[OllamaModelOut] = Field(default_factory=list[OllamaModelOut])


class ConfigOut(BaseModel):
    timezone: str
    wip_limit: int
    due_soon_days: int
    classifier_enabled: bool


class ChannelsOut(BaseModel):
    areas: list[AreaOut]
    channels: list[ChannelOut]


class TaskOut(Out):
    id: str
    channel_id: str
    title: str
    description: str | None
    status: TaskStatus
    position: float
    due_at: datetime | None
    priority: int | None
    created_at: datetime
    updated_at: datetime


class TaskCreate(BaseModel):
    channel_id: str
    title: str = Field(min_length=1)
    description: str | None = None
    status: TaskStatus = TaskStatus.TODO
    due_at: AwareDatetime | None = None
    priority: int | None = Field(default=None, ge=0, le=3)


class TaskUpdate(BaseModel):
    channel_id: str | None = None
    title: str | None = Field(default=None, min_length=1)
    description: str | None = None
    status: TaskStatus | None = None
    due_at: AwareDatetime | None = None
    priority: int | None = Field(default=None, ge=0, le=3)


class TaskMove(BaseModel):
    status: TaskStatus
    after_id: str | None = None
    before_id: str | None = None


class EventOut(Out):
    id: str
    channel_id: str
    title: str
    all_day: bool
    starts_at: datetime | None
    ends_at: datetime | None
    start_date: date | None
    end_date: date | None
    location: str | None
    rrule: str | None
    calendar_id: str | None
    created_at: datetime
    updated_at: datetime


class EventCreate(BaseModel):
    channel_id: str
    title: str = Field(min_length=1)
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    start_date: date | None = None
    end_date: date | None = None
    location: str | None = None
    rrule: str | None = None
    calendar_id: str | None = None


class EventUpdate(BaseModel):
    channel_id: str | None = None
    title: str | None = Field(default=None, min_length=1)
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    start_date: date | None = None
    end_date: date | None = None
    location: str | None = None
    rrule: str | None = None


class InboxOut(Out):
    id: str
    channel_id: str | None
    raw_text: str
    captured_via: str
    status: InboxStatus
    suggestion_json: dict[str, Any] | None
    confidence: float | None
    created_at: datetime
    updated_at: datetime


class InboxCreate(BaseModel):
    raw_text: str = Field(min_length=1)
    captured_via: str = "api"


class InboxUpdate(BaseModel):
    status: InboxStatus | None = None
    suggestion_json: dict[str, Any] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class InboxPage(BaseModel):
    items: list[InboxOut]
    next_cursor: str | None


class ApprovalOut(Out):
    id: str
    requested_by: str
    channel_id: str | None
    action: str
    payload_json: dict[str, Any]
    summary: str
    reason: str | None
    status: ApprovalStatus
    error: str | None
    created_at: datetime
    resolved_at: datetime | None


class RefOut(BaseModel):
    """The object a message renders as a card (PLAN P4); at most one is set."""

    inbox_item: InboxOut | None = None
    task: TaskOut | None = None
    event: EventOut | None = None
    approval: ApprovalOut | None = None


class MessageOut(Out):
    id: str
    channel_id: str
    thread_root_id: str | None
    author_type: AuthorType
    author_id: str | None
    body: str
    ref_type: str | None
    ref_id: str | None
    pinned: bool
    created_at: datetime
    reply_count: int = 0
    ref: RefOut = Field(default_factory=RefOut)


class MessagePage(BaseModel):
    items: list[MessageOut]
    next_cursor: str | None


class ThreadOut(BaseModel):
    root: MessageOut
    replies: list[MessageOut]


class MessageCreate(BaseModel):
    body: str = Field(min_length=1, max_length=10_000)
    thread_root_id: str | None = None


class MessageUpdate(BaseModel):
    pinned: bool | None = None


class PromoteFields(BaseModel):
    """User corrections applied when accepting a suggestion or converting a message."""

    title: str | None = Field(default=None, min_length=1, max_length=500)
    due_at: AwareDatetime | None = None
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    start_date: date | None = None
    end_date: date | None = None
    channel_id: str | None = None


class InboxAccept(PromoteFields):
    type: Literal["task", "event", "idea"] | None = None


class MessageConvert(PromoteFields):
    kind: Literal["task", "event"]


class PromotedOut(BaseModel):
    object_type: str
    id: str


class ActivityOut(Out):
    id: str
    object_type: str
    object_id: str
    action: str
    before_json: dict[str, Any] | None
    after_json: dict[str, Any] | None
    actor: str
    created_at: datetime


class TodayOut(BaseModel):
    events: list[EventOut]
    due_tasks: list[TaskOut]
    inbox_count: int


NOT_NULL_FIELDS = {
    "weekdays",
    "channel_id",
    "title",
    "status",
    "name",
    "kind",
    "area_id",
    "sort_order",
}


def _changes(body: BaseModel) -> dict[str, Any]:
    """Only the fields the client sent; explicit null clears optional fields."""
    changes = body.model_dump(exclude_unset=True)
    cleared = sorted(k for k in NOT_NULL_FIELDS & changes.keys() if changes[k] is None)
    if cleared:
        raise services.InvalidError(f"cannot clear required field(s): {', '.join(cleared)}")
    return changes


# --- routes -------------------------------------------------------------------


@router.get("/health")
async def health(session: Session) -> dict[str, str]:
    journal_mode = (await session.execute(text("PRAGMA journal_mode"))).scalar_one()
    return {"status": "ok", "db": "ok", "journal_mode": journal_mode}


@router.get("/channels")
async def list_channels(session: Session) -> ChannelsOut:
    return ChannelsOut(
        areas=[AreaOut.model_validate(a) for a in await services.list_areas(session)],
        channels=[ChannelOut.model_validate(c) for c in await services.list_channels(session)],
    )


@router.post("/areas", status_code=status.HTTP_201_CREATED)
async def create_area(session: Session, body: AreaCreate) -> AreaOut:
    area = await services.create_area(session, actor=USER, **body.model_dump())
    return AreaOut.model_validate(area)


@router.patch("/areas/{area_id}")
async def update_area(session: Session, area_id: str, body: AreaUpdate) -> AreaOut:
    area = await services.update_area(session, area_id, _changes(body), USER)
    return AreaOut.model_validate(area)


@router.delete("/areas/{area_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_area(session: Session, area_id: str) -> None:
    await services.delete_area(session, area_id, USER)


@router.post("/channels", status_code=status.HTTP_201_CREATED)
async def create_channel(session: Session, body: ChannelCreate) -> ChannelOut:
    channel = await services.create_channel(session, actor=USER, **body.model_dump())
    return ChannelOut.model_validate(channel)


@router.patch("/channels/{channel_id}")
async def update_channel(session: Session, channel_id: str, body: ChannelUpdate) -> ChannelOut:
    channel = await services.update_channel(session, channel_id, _changes(body), USER)
    return ChannelOut.model_validate(channel)


@router.delete("/channels/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel(session: Session, channel_id: str, force: bool = False) -> None:
    await services.delete_channel(session, channel_id, USER, force=force)


async def _messages_out(session: AsyncSession, messages: list[Message]) -> list[MessageOut]:
    """Embeds each message's referenced object and reply count in two small queries."""
    counts = await services.reply_counts(session, [m.id for m in messages])
    by_type: dict[str, set[str]] = {}
    for m in messages:
        if m.ref_type and m.ref_id:
            by_type.setdefault(m.ref_type, set()).add(m.ref_id)
    models = {
        "inbox_item": (InboxItem, InboxOut),
        "task": (Task, TaskOut),
        "event": (Event, EventOut),
        "approval": (Approval, ApprovalOut),
    }
    refs: dict[tuple[str, str], BaseModel] = {}
    for ref_type, ids in by_type.items():
        if ref_type not in models:
            continue
        model, schema = models[ref_type]
        for obj in (await session.scalars(select(model).where(model.id.in_(ids)))).all():
            refs[(ref_type, obj.id)] = schema.model_validate(obj)
    out: list[MessageOut] = []
    for m in messages:
        item = MessageOut.model_validate(m)
        item.reply_count = counts.get(m.id, 0)
        if m.ref_type and m.ref_id and (ref := refs.get((m.ref_type, m.ref_id))):
            item.ref = RefOut.model_validate({m.ref_type: ref})
        out.append(item)
    return out


def _classify_later(
    request: Request, background: BackgroundTasks, config: Settings, item_id: str | None
) -> None:
    classifier: Classifier | None = request.app.state.classifier
    if item_id and classifier is not None:
        background.add_task(
            chat.classify_item,
            request.app.state.sessionmaker,
            classifier,
            item_id,
            config,
            datetime.now(UTC),
        )


@router.get("/channels/{channel_id}/messages")
async def list_messages(
    session: Session,
    channel_id: str,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> MessagePage:
    await services.get_channel(session, channel_id)
    messages, next_cursor = await services.list_messages(
        session, channel_id, cursor=cursor, limit=limit
    )
    return MessagePage(items=await _messages_out(session, messages), next_cursor=next_cursor)


@router.post("/channels/{channel_id}/messages", status_code=status.HTTP_201_CREATED)
async def post_message(
    request: Request,
    background: BackgroundTasks,
    session: Session,
    config: Config,
    channel_id: str,
    body: MessageCreate,
) -> MessageOut:
    message, pending = await chat.post_message(
        session,
        channel_id=channel_id,
        body=body.body,
        thread_root_id=body.thread_root_id,
        now=datetime.now(UTC),
        settings=config,
    )
    _classify_later(request, background, config, pending)
    [out] = await _messages_out(session, [message])
    return out


@router.get("/messages/{message_id}/thread")
async def get_thread(session: Session, message_id: str) -> ThreadOut:
    root, replies = await services.list_thread(session, message_id)
    [root_out, *reply_out] = await _messages_out(session, [root, *replies])
    return ThreadOut(root=root_out, replies=reply_out)


@router.patch("/messages/{message_id}")
async def update_message(session: Session, message_id: str, body: MessageUpdate) -> MessageOut:
    changes = _changes(body)
    if changes.get("pinned") is None:
        changes.pop("pinned", None)
    message = await services.update_message(session, message_id, changes, USER)
    [out] = await _messages_out(session, [message])
    return out


@router.post("/messages/{message_id}/convert")
async def convert_message(session: Session, message_id: str, body: MessageConvert) -> PromotedOut:
    fields = body.model_dump(exclude_unset=True, exclude={"kind"})
    obj = await services.convert_message(
        session, message_id, kind=body.kind, fields=fields, actor=USER
    )
    return PromotedOut(object_type=obj.__tablename__, id=obj.id)


@router.post("/inbox/{item_id}/accept")
async def accept_inbox_item(session: Session, item_id: str, body: InboxAccept) -> PromotedOut:
    overrides = body.model_dump(exclude_unset=True)
    obj = await services.accept_inbox_item(session, item_id, actor=USER, overrides=overrides)
    return PromotedOut(object_type=obj.__tablename__, id=obj.id)


@router.post("/inbox/{item_id}/classify", status_code=status.HTTP_202_ACCEPTED)
async def reclassify_inbox_item(
    request: Request, background: BackgroundTasks, session: Session, config: Config, item_id: str
) -> InboxOut:
    """Retry after a failure (or after configuring a model): resets to `new` and queues."""
    if request.app.state.classifier is None:
        raise services.InvalidError("분류 모델이 설정되지 않았어요 (ARGOS_CLASSIFIER_MODEL)")
    item = await services.update_inbox_item(
        session, item_id, {"status": InboxStatus.NEW, "suggestion_json": None}, USER
    )
    _classify_later(request, background, config, item.id)
    return InboxOut.model_validate(item)


async def _classifier_settings(request: Request, session: AsyncSession) -> ClassifierSettingsOut:
    config: Settings = request.app.state.settings
    overrides = await services.get_settings_overrides(session)
    if "classifier_model" in overrides:
        source = "app" if config.classifier_model else "none"
    else:
        source = "env" if config.classifier_model else "none"
    return ClassifierSettingsOut(
        provider=config.classifier_provider,
        model=config.classifier_model,
        source=source,
        enabled=request.app.state.classifier is not None,
    )


def _today(config: Settings) -> date:
    return datetime.now(config.zoneinfo).date()


@router.get("/approvals")
async def list_approvals(
    session: Session, status: ApprovalStatus | None = None
) -> list[ApprovalOut]:
    return [ApprovalOut.model_validate(a) for a in await services.list_approvals(session, status)]


@router.post("/approvals/{approval_id}/approve")
async def approve(session: Session, approval_id: str) -> ApprovalOut:
    approval = await services.resolve_approval(session, approval_id, approve=True, actor=USER)
    return ApprovalOut.model_validate(approval)


@router.post("/approvals/{approval_id}/reject")
async def reject(session: Session, approval_id: str) -> ApprovalOut:
    approval = await services.resolve_approval(session, approval_id, approve=False, actor=USER)
    return ApprovalOut.model_validate(approval)


@router.get("/routines")
async def list_routines(session: Session, config: Config, day: date | None = None) -> RoutinesOut:
    today = _today(config)
    rows = await services.list_routines(session, day=day or today, today=today)
    return RoutinesOut(
        day=day or today,
        today=today,
        routines=[
            RoutineOut(
                id=r.id,
                title=r.title,
                weekdays=r.weekdays,
                sort_order=r.sort_order,
                scheduled=scheduled,
                done=done,
                streak=streak,
            )
            for r, scheduled, done, streak in rows
        ],
    )


@router.post("/routines", status_code=status.HTTP_201_CREATED)
async def create_routine(session: Session, body: RoutineCreate) -> PromotedOut:
    routine = await services.create_routine(session, actor=USER, **body.model_dump())
    return PromotedOut(object_type="routine", id=routine.id)


@router.patch("/routines/{routine_id}", status_code=status.HTTP_204_NO_CONTENT)
async def update_routine(session: Session, routine_id: str, body: RoutineUpdate) -> None:
    await services.update_routine(session, routine_id, _changes(body), USER)


@router.delete("/routines/{routine_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_routine(session: Session, routine_id: str) -> None:
    await services.delete_routine(session, routine_id, USER)


@router.put("/routines/{routine_id}/checks/{day}", status_code=status.HTTP_204_NO_CONTENT)
async def set_routine_check(
    session: Session, config: Config, routine_id: str, day: date, body: RoutineCheckIn
) -> None:
    await services.set_routine_check(
        session, routine_id, day=day, done=body.done, today=_today(config), actor=USER
    )


@router.get("/settings/classifier")
async def get_classifier_settings(request: Request, session: Session) -> ClassifierSettingsOut:
    return await _classifier_settings(request, session)


@router.put("/settings/classifier")
async def put_classifier_settings(
    request: Request, session: Session, body: ClassifierSettingsIn
) -> ClassifierSettingsOut:
    """Saves the model choice and swaps the classifier without a restart."""
    model = (body.model or "").strip()
    base: Settings = request.app.state.base_settings
    if model and base.classifier_provider == "ollama":
        try:
            installed = {m.name for m in await classifier.list_ollama_models(base)}
        except classifier.ClassifierError as exc:
            raise services.InvalidError("Ollama에 연결할 수 없어요") from exc
        if model not in installed:
            raise services.InvalidError(f"Ollama에 설치되지 않은 모델이에요: {model}")
    await services.set_setting(session, "classifier_model", model, USER)
    overrides = await services.get_settings_overrides(session)
    request.app.state.settings = classifier.apply_overrides(base, overrides)
    request.app.state.classifier = classifier.build_classifier(request.app.state.settings)
    return await _classifier_settings(request, session)


@router.get("/settings/classifier/models")
async def list_classifier_models(request: Request) -> OllamaModelsOut:
    base: Settings = request.app.state.base_settings
    if base.classifier_provider != "ollama":
        return OllamaModelsOut(
            reachable=False, error="모델 목록은 ollama provider에서만 볼 수 있어요"
        )
    try:
        models = await classifier.list_ollama_models(base)
    except classifier.ClassifierError:
        return OllamaModelsOut(
            reachable=False, error="Ollama에 연결할 수 없어요. 실행 중인지 확인해 주세요."
        )
    return OllamaModelsOut(
        reachable=True,
        models=[
            OllamaModelOut(name=m.name, remote=m.remote, parameter_size=m.parameter_size)
            for m in models
        ],
    )


@router.get("/config")
async def get_config(request: Request, config: Config) -> ConfigOut:
    return ConfigOut(
        timezone=config.timezone,
        wip_limit=config.wip_limit,
        due_soon_days=config.due_soon_days,
        classifier_enabled=request.app.state.classifier is not None,
    )


@router.get("/tasks")
async def list_tasks(
    session: Session, channel_id: str | None = None, status: TaskStatus | None = None
) -> list[TaskOut]:
    tasks = await services.list_tasks(session, channel_id=channel_id, status=status)
    return [TaskOut.model_validate(t) for t in tasks]


@router.post("/tasks", status_code=status.HTTP_201_CREATED)
async def create_task(session: Session, body: TaskCreate) -> TaskOut:
    task = await services.create_task(session, actor=USER, **body.model_dump())
    return TaskOut.model_validate(task)


@router.get("/tasks/{task_id}")
async def get_task(session: Session, task_id: str) -> TaskOut:
    return TaskOut.model_validate(await services.get_task(session, task_id))


@router.get("/tasks/{task_id}/activity")
async def task_activity(session: Session, task_id: str) -> list[ActivityOut]:
    return [ActivityOut.model_validate(a) for a in await services.list_activity(session, task_id)]


@router.patch("/tasks/{task_id}")
async def update_task(session: Session, task_id: str, body: TaskUpdate) -> TaskOut:
    task = await services.update_task(session, task_id, _changes(body), USER)
    return TaskOut.model_validate(task)


@router.post("/tasks/{task_id}/move")
async def move_task(session: Session, task_id: str, body: TaskMove) -> TaskOut:
    task = await services.move_task(session, task_id, actor=USER, **body.model_dump())
    return TaskOut.model_validate(task)


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(session: Session, task_id: str) -> None:
    await services.delete_task(session, task_id, USER)


@router.get("/events")
async def list_events(
    session: Session,
    config: Config,
    start: AwareDatetime,
    end: AwareDatetime,
    channel_id: str | None = None,
) -> list[EventOut]:
    events = await services.list_events(
        session, start=start, end=end, tz=config.zoneinfo, channel_id=channel_id
    )
    return [EventOut.model_validate(e) for e in events]


@router.post("/events", status_code=status.HTTP_201_CREATED)
async def create_event(session: Session, body: EventCreate) -> EventOut:
    event = await services.create_event(session, actor=USER, **body.model_dump())
    return EventOut.model_validate(event)


@router.get("/events/{event_id}")
async def get_event(session: Session, event_id: str) -> EventOut:
    return EventOut.model_validate(await services.get_event(session, event_id))


@router.patch("/events/{event_id}")
async def update_event(session: Session, event_id: str, body: EventUpdate) -> EventOut:
    event = await services.update_event(session, event_id, _changes(body), USER)
    return EventOut.model_validate(event)


@router.delete("/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_event(session: Session, event_id: str) -> None:
    await services.delete_event(session, event_id, USER)


@router.get("/inbox")
async def list_inbox(
    session: Session,
    status: Annotated[list[InboxStatus] | None, Query()] = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> InboxPage:
    items, next_cursor = await services.list_inbox(
        session, statuses=status, cursor=cursor, limit=limit
    )
    return InboxPage(items=[InboxOut.model_validate(i) for i in items], next_cursor=next_cursor)


@router.post("/inbox", status_code=status.HTTP_201_CREATED)
async def create_inbox_item(session: Session, body: InboxCreate) -> InboxOut:
    item = await services.create_inbox_item(session, actor=USER, **body.model_dump())
    return InboxOut.model_validate(item)


@router.patch("/inbox/{item_id}")
async def update_inbox_item(session: Session, item_id: str, body: InboxUpdate) -> InboxOut:
    item = await services.update_inbox_item(session, item_id, _changes(body), USER)
    return InboxOut.model_validate(item)


@router.delete("/inbox/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_inbox_item(session: Session, item_id: str) -> None:
    await services.delete_inbox_item(session, item_id, USER)


@router.get("/today")
async def today(session: Session, config: Config) -> TodayOut:
    result = await services.get_today(
        session, now=datetime.now(UTC), tz=config.zoneinfo, due_soon_days=config.due_soon_days
    )
    return TodayOut(
        events=[EventOut.model_validate(e) for e in result["events"]],
        due_tasks=[TaskOut.model_validate(t) for t in result["due_tasks"]],
        inbox_count=result["inbox_count"],
    )


ws_router = APIRouter()


@ws_router.websocket("/ws")
async def websocket(ws: WebSocket) -> None:
    await hub.connect(ws)
    try:
        while True:
            await ws.receive_text()  # client → server events arrive from Phase 5 (run.cancel)
    except WebSocketDisconnect:
        hub.disconnect(ws)


# --- errors (PLAN §7.1: {"error": {"code", "message"}}) -------------------------


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status_code)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(services.NotFoundError)
    async def not_found(_: Request, exc: services.NotFoundError) -> JSONResponse:
        return _error(404, "not_found", str(exc))

    @app.exception_handler(services.ConflictError)
    async def conflict(_: Request, exc: services.ConflictError) -> JSONResponse:
        return _error(409, "conflict", str(exc))

    @app.exception_handler(services.InvalidError)
    async def invalid(_: Request, exc: services.InvalidError) -> JSONResponse:
        return _error(422, "invalid", str(exc))

    @app.exception_handler(RequestValidationError)
    async def validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        message = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        return _error(422, "validation_error", message)

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        return _error(exc.status_code, "http_error", str(exc.detail))
