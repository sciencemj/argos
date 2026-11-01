"""Private chat (PLAN Phase 21): a local model answers and Argos keeps no record.

Unlike every other feature this does not go through `services`: no rows, no websocket
events, no activity log, no attachment files. The conversation lives in the client's
memory and comes along with each request; files arrive once as base64 JSON (multipart
would spool anything past 1MB to a temporary file) and go back as processed parts.
Nothing here logs request bodies, model output, file names or exception text."""

import asyncio
import base64
import binascii
import json
import logging
import threading
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, Literal, cast

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from openai import APIConnectionError
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from argos import agents, attachments, providers, services
from argos.api import Session
from argos.config import Settings
from argos.models import Agent
from argos.runner import language_line, time_line

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1")

REFUSED = "비공개 대화는 로컬 모델에서만 쓸 수 있어요"
BAD_BODY = "요청 형식이 맞지 않아요"
NO_ANSWER = "답을 받지 못했어요"


class PrivatePart(BaseModel):
    kind: Literal["text", "image", "pdf"]
    name: str = Field(max_length=255)
    text: str | None = None
    data: str | None = None  # base64
    mime: str | None = Field(default=None, max_length=100)


class PrivateTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: str
    parts: list[PrivatePart] = Field(default_factory=list[PrivatePart])


class PrivateChatIn(BaseModel):
    turns: list[PrivateTurn] = Field(min_length=1, max_length=200)


class PrivateFile(BaseModel):
    name: str = Field(max_length=255)
    data: str  # base64


class PrivateFilesIn(BaseModel):
    files: list[PrivateFile] = Field(min_length=1, max_length=attachments.MAX_FILES)


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


async def _read[T: BaseModel](request: Request, model: type[T]) -> T | None:
    """The body, validated without echoing it back: pydantic messages can quote input."""
    try:
        return model.model_validate_json(await request.body())
    except ValidationError:
        return None


async def _target(
    session: AsyncSession, settings: Settings, name: str
) -> tuple[Agent, providers.ProviderConfig, str] | JSONResponse:
    agent = await services.find_agent(session, name)
    if agent is None:
        return _error(404, "not_found", f"agent {name} not found")
    if not agents.private_allowed(agent, settings):
        return _error(422, "invalid", REFUSED)
    p, model = agents.local_target(agent, settings)
    if p.kind == "ollama":
        # Ask Ollama itself: a cloud model copied under a local name still runs remotely.
        try:
            if await providers.remote_host(p, model) is not None:
                return _error(422, "invalid", REFUSED)
        except providers.ProviderUnreachable:
            return _error(422, "unreachable", providers.unreachable_text(p))
        except providers.ProviderError:
            return _error(422, "invalid", REFUSED)
    return agent, p, model


@router.post("/agents/{name}/private/files", response_model=list[PrivatePart])
async def private_files(request: Request, session: Session, name: str) -> Any:
    settings: Settings = request.app.state.settings
    target = await _target(session, settings, name)
    if isinstance(target, JSONResponse):
        return target
    body = await _read(request, PrivateFilesIn)
    if body is None:
        return _error(422, "validation_error", BAD_BODY)
    limit = settings.attachment_max_mb * 1024 * 1024
    raw: list[tuple[str, bytes]] = []
    for item in body.files:
        try:
            data = base64.b64decode(item.data, validate=True)
        except (binascii.Error, ValueError):
            return _error(422, "validation_error", BAD_BODY)
        if len(data) > limit:
            return _error(413, "too_large", f"{settings.attachment_max_mb}MB까지 올릴 수 있어요")
        raw.append((attachments.safe_name(item.name), data))
    parts = await asyncio.to_thread(_read_files, raw)
    return [_out(name, part) for (name, _), part in zip(raw, parts, strict=True)]


_quiet = threading.Lock()


def _read_files(raw: list[tuple[str, bytes]]) -> list[attachments.AgentPart]:
    """pypdf logs warnings about malformed PDFs that quote the PDF's own text; silence
    it while a private file is read (the lock keeps the level change to one reader)."""
    pdf_log = logging.getLogger("pypdf")
    with _quiet:
        level = pdf_log.level
        pdf_log.setLevel(logging.CRITICAL + 1)
        try:
            return [attachments.part_from_bytes(n, d) for n, d in raw]
        finally:
            pdf_log.setLevel(level)


def _out(name: str, part: attachments.AgentPart) -> PrivatePart:
    match part:
        case attachments.ImagePart():
            data = base64.b64encode(part.data).decode()
            return PrivatePart(kind="image", name=name, data=data, mime=part.mime)
        case attachments.PdfPart():
            data = base64.b64encode(part.data).decode()
            return PrivatePart(kind="pdf", name=name, data=data, mime=part.mime)
        case attachments.TextPart():
            return PrivatePart(kind="text", name=name, text=part.text)


def _system(settings: Settings, agent: Agent) -> str:
    now = datetime.now(UTC).astimezone(settings.zoneinfo)
    lines = [time_line(settings, now), language_line(settings)]
    if agent.system_prompt:
        lines.append(agent.system_prompt)
    return "\n".join(lines)


def _messages(
    settings: Settings, agent: Agent, turns: list[PrivateTurn], vision: bool
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [{"role": "system", "content": _system(settings, agent)}]
    for turn in turns:
        if turn.role == "assistant":
            messages.append({"role": "assistant", "content": turn.text})
            continue
        texts = [turn.text] + [p.text for p in turn.parts if p.kind != "image" and p.text]
        text = "\n\n".join(t for t in texts if t)
        images = [p for p in turn.parts if p.kind == "image" and p.data and p.mime]
        if images and vision:
            content: list[dict[str, Any]] = [{"type": "text", "text": text}]
            content += [
                {"type": "image_url", "image_url": {"url": f"data:{p.mime};base64,{p.data}"}}
                for p in images
            ]
            messages.append({"role": "user", "content": content})
        else:
            note = f"\n\n{agents.NO_VISION}" if images else ""
            messages.append({"role": "user", "content": text + note})
    return messages


def _line(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode()


@router.post("/agents/{name}/private")
async def private_chat(request: Request, session: Session, name: str) -> Any:
    settings: Settings = request.app.state.settings
    target = await _target(session, settings, name)
    if isinstance(target, JSONResponse):
        return target
    agent, p, model = target
    body = await _read(request, PrivateChatIn)
    if body is None:
        return _error(422, "validation_error", BAD_BODY)
    has_images = any(part.kind == "image" for t in body.turns for part in t.parts)
    if not has_images:
        vision = False
    elif p.kind == "ollama":
        vision = await providers.vision(p, model)
    else:
        vision = agent.send_images
    messages = _messages(settings, agent, body.turns, vision)
    client = providers.private_client(p, timeout=settings.agent_timeout)
    extra = {"reasoning_effort": "none"} if p.kind == "ollama" else None

    async def answer() -> AsyncIterator[bytes]:
        stream: Any = None
        try:
            stream = await client.chat.completions.create(
                model=model, messages=cast(Any, messages), stream=True, extra_body=extra
            )
            async for chunk in stream:
                if chunk.choices and (text := chunk.choices[0].delta.content):
                    yield _line({"token": text})
            yield _line({"done": True})
        except APIConnectionError:
            yield _line({"error": providers.unreachable_text(p)})
        except Exception as exc:  # the type only: messages can quote the conversation
            log.warning("private chat failed: %s", type(exc).__name__)
            yield _line({"error": NO_ANSWER})
        finally:
            if stream is not None:
                await stream.close()
            await client.close()

    return StreamingResponse(answer(), media_type="application/x-ndjson")
