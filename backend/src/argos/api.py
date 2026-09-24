from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException

from argos import services
from argos.config import Settings
from argos.db import session_scope
from argos.models import ChannelKind, InboxStatus, TaskStatus

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


class TodayOut(BaseModel):
    events: list[EventOut]
    due_tasks: list[TaskOut]
    inbox_count: int


NOT_NULL_FIELDS = {"channel_id", "title", "status"}


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


# --- errors (PLAN §7.1: {"error": {"code", "message"}}) -------------------------


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status_code)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(services.NotFoundError)
    async def not_found(_: Request, exc: services.NotFoundError) -> JSONResponse:
        return _error(404, "not_found", str(exc))

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
