"""Inbox classifier (PLAN Phase 3): free text → a structured suggestion.

A plain client on purpose; Phase 5 swaps it for the default agent behind the same
`Classifier` protocol. Providers `ollama` and `hermes` both speak the OpenAI API, so one
implementation covers them; a direct cloud provider is intentionally not built yet.
"""

import json
import logging
import re
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

import httpx2
from openai import AsyncOpenAI, BadRequestError
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from argos import commands
from argos.config import Settings

log = logging.getLogger(__name__)

DEFAULT_BASE_URLS = {
    "ollama": "http://127.0.0.1:11434/v1",
    "hermes": "http://127.0.0.1:8642/v1",
}
WEEKDAY_KO = "월화수목금토일"


class Suggestion(BaseModel):
    type: Literal["task", "event", "idea", "study_note"]
    title: str = Field(min_length=1, max_length=500)
    due_at: datetime | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    all_day_date: date | None = None
    channel_hint: str | None = None
    tags: list[str] = Field(default_factory=list[str])
    summary: str = ""
    confidence: float = Field(ge=0, le=1)

    @field_validator("channel_hint")
    @classmethod
    def _strip_hash(cls, value: str | None) -> str | None:
        return value.strip().lstrip("#").strip() or None if value else None

    @model_validator(mode="after")
    def _event_has_time(self) -> "Suggestion":
        if self.type == "event" and self.starts_at is None and self.all_day_date is None:
            raise ValueError("event suggestion without starts_at or all_day_date")
        return self

    def localize(self, tz: ZoneInfo) -> "Suggestion":
        """Models sometimes drop the offset; read naive times as local wall-clock time."""

        def fix(value: datetime | None) -> datetime | None:
            return value.replace(tzinfo=tz) if value and value.tzinfo is None else value

        return self.model_copy(
            update={
                "due_at": fix(self.due_at),
                "starts_at": fix(self.starts_at),
                "ends_at": fix(self.ends_at),
            }
        )


# JSON schema sent to the model: plain strings for dates keep it portable across servers.
OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": ["task", "event", "idea", "study_note"]},
        "title": {"type": "string"},
        "due_at": {"type": ["string", "null"]},
        "starts_at": {"type": ["string", "null"]},
        "ends_at": {"type": ["string", "null"]},
        "all_day_date": {"type": ["string", "null"]},
        "channel_hint": {"type": ["string", "null"]},
        "tags": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": [
        "type",
        "title",
        "due_at",
        "starts_at",
        "ends_at",
        "all_day_date",
        "channel_hint",
        "tags",
        "summary",
        "confidence",
    ],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class ClassifyContext:
    now: datetime
    tz: ZoneInfo
    channel_name: str | None
    channel_kind: str | None
    channel_names: list[str]
    personal_channel: str | None = None  # the built-in #일상 channel's current name


class ClassifierError(Exception):
    pass


class Classifier(Protocol):
    async def classify(self, text: str, context: ClassifyContext) -> Suggestion: ...


def _calendar(local: datetime) -> str:
    """Two weeks of dates with Korean labels: small models get relative dates wrong
    ("다음주 수요일"), so the prompt hands them the lookup table instead of arithmetic."""
    today = local.date()
    this_monday = today - timedelta(days=today.weekday())
    lines: list[str] = []
    for offset in range(14):
        day = today + timedelta(days=offset)
        week = "이번주" if day < this_monday + timedelta(days=7) else "다음주"
        if (day - this_monday).days >= 14:
            week = "다다음주"
        label = {0: " = 오늘", 1: " = 내일", 2: " = 모레"}.get(offset, "")
        lines.append(f"- {day:%Y-%m-%d}: {week} {WEEKDAY_KO[day.weekday()]}요일{label}")
    return "\n".join(lines)


def build_prompt(text: str, ctx: ClassifyContext) -> list[dict[str, str]]:
    local = ctx.now.astimezone(ctx.tz)
    today = f"{local:%Y-%m-%d} ({WEEKDAY_KO[local.weekday()]}) {local:%H:%M} {ctx.tz.key}"
    if ctx.channel_kind in ("course", "project", "personal"):
        channel_rule = (
            f'입력은 "#{ctx.channel_name}" 채널에서 들어왔다. 이 채널로 이미 정해진 것으로 보고 '
            f'channel_hint는 "{ctx.channel_name}"로 둔다.'
        )
    else:
        channel_rule = (
            "입력한 채널이 과목·프로젝트가 아니다. 입력에 채널 이름이 직접 나오거나 그 과목 내용이 "
            "분명할 때만 channel_hint에 그 이름을 넣는다. 조금이라도 애매하면 null."
        )
        if ctx.personal_channel:
            channel_rule += (
                f" 과목·프로젝트와 무관한 개인 일상(약속, 병원, 운동, 장보기, 집안일 등)이면 "
                f'channel_hint는 "{ctx.personal_channel}".'
            )
    system = f"""너는 개인 일정·할 일 앱의 입력 분류기다.
사용자가 적은 한 줄을 JSON 하나로만 답한다.

현재 시각: {today}
날짜표(상대 날짜는 반드시 이 표에서 찾는다. 주는 월요일에 시작):
{_calendar(local)}

채널 목록: {", ".join(ctx.channel_names) or "(없음)"}
{channel_rule}

type:
- task: 해야 할 일. 마감 표현("~까지", 날짜)이 있을 때만 due_at, 없으면 null.
- event: 특정 시각·날짜에 일어나는 일정(수업, 면담, 시험, 모임). 시간이 있으면 starts_at
  (알면 ends_at), 날짜만 있으면 all_day_date(YYYY-MM-DD).
- idea: 나중에 해볼 생각("~해보기", "~하면 좋겠다"). 날짜를 지어내지 않는다.
- study_note: 공부한 내용·정리할 개념.

규칙:
- 입력에 없는 날짜·시각을 만들어 내지 않는다.
- 시각은 오프셋을 붙인 ISO 8601(예: 2026-09-26T23:59:00+09:00). "~까지"에 시각이 없으면
  그날 23:59.
- title은 입력을 짧게 다듬은 명사구(예: "과제2 제출"). 내용을 덧붙이지 않는다.
- summary는 한 문장.
- confidence는 0~1. 유형·날짜·채널이 모두 입력에서 분명하면 0.9 이상, 무엇이든 추측했으면
  0.7 이하.
- 해당 없는 필드는 null, tags는 없으면 []."""
    return [{"role": "system", "content": system}, {"role": "user", "content": text}]


def _parse_json(content: str) -> dict[str, Any]:
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
    if fenced := re.search(r"```(?:json)?\s*(\{.*\})\s*```", content, flags=re.S):
        content = fenced.group(1)
    elif (start := content.find("{")) >= 0:
        content = content[start : content.rfind("}") + 1]
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError("classifier did not return a JSON object")
    return data  # pyright: ignore[reportUnknownVariableType]


class OpenAICompatClassifier:
    """Ollama or Hermes through their OpenAI-compatible endpoints, with a small cache
    (PLAN §8.5) keyed by text, channel and day."""

    def __init__(
        self,
        client: AsyncOpenAI,
        model: str,
        *,
        reasoning_effort: str | None = None,
        cache_size: int = 256,
    ) -> None:
        self._client = client
        self._model = model
        self._extra: dict[str, Any] = (
            {"reasoning_effort": reasoning_effort} if reasoning_effort else {}
        )
        self._cache: OrderedDict[tuple[str, str | None, str], Suggestion] = OrderedDict()
        self._cache_size = cache_size
        self._schema_supported = True

    async def classify(self, text: str, context: ClassifyContext) -> Suggestion:
        key = (text, context.channel_name, f"{context.now.astimezone(context.tz):%Y-%m-%d}")
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        suggestion = await self._ask(text, context)
        self._cache[key] = suggestion
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return suggestion

    async def _ask(self, text: str, context: ClassifyContext) -> Suggestion:
        messages: Any = build_prompt(text, context)
        content: str | None
        try:
            if self._schema_supported:
                try:
                    response = await self._client.chat.completions.create(
                        model=self._model,
                        messages=messages,
                        response_format={
                            "type": "json_schema",
                            "json_schema": {"name": "suggestion", "schema": OUTPUT_SCHEMA},
                        },
                        temperature=0,
                        extra_body=self._extra,
                    )
                except BadRequestError:
                    # Server without structured output: fall back to prompt-only JSON.
                    self._schema_supported = False
                    return await self._ask(text, context)
            else:
                response = await self._client.chat.completions.create(
                    model=self._model, messages=messages, temperature=0, extra_body=self._extra
                )
            content = response.choices[0].message.content
        except Exception as exc:
            raise ClassifierError(f"classifier request failed: {type(exc).__name__}") from exc
        if not content:
            raise ClassifierError("classifier returned no content")
        try:
            return Suggestion.model_validate(_parse_json(content)).localize(context.tz)
        except (ValueError, ValidationError) as exc:
            raise ClassifierError(f"unusable classifier output: {exc}") from exc


def anchor_dates(suggestion: Suggestion, text: str, ctx: ClassifyContext) -> Suggestion:
    """Replaces the model's date with the one the deterministic grammar reads from the
    text, when it finds one; small models miscount "다음주 수요일". A clock time in the
    text wins too, otherwise the model's time (or 23:59 for deadlines) is kept."""
    today = ctx.now.astimezone(ctx.tz).date()
    day = commands.find_date(text, today)
    if day is None:
        return suggestion
    clock = commands.find_time(text)

    def on_day(value: datetime | None, fallback: time) -> datetime:
        local = value.astimezone(ctx.tz).time() if value else fallback
        return datetime.combine(day, clock or local, ctx.tz)

    if suggestion.type == "event":
        if suggestion.starts_at is None and clock is None:
            return suggestion.model_copy(update={"all_day_date": day})
        start = on_day(suggestion.starts_at, time(9, 0))
        length = (
            suggestion.ends_at - suggestion.starts_at
            if suggestion.ends_at and suggestion.starts_at
            else timedelta(hours=1)
        )
        return suggestion.model_copy(
            update={"starts_at": start, "ends_at": start + length, "all_day_date": None}
        )
    if suggestion.type == "task":
        return suggestion.model_copy(
            update={"due_at": on_day(suggestion.due_at, commands.DEFAULT_DUE)}
        )
    return suggestion


def build_classifier(settings: Settings) -> Classifier | None:
    """None when no model is configured: text still lands in the inbox, unclassified."""
    if not settings.classifier_model:
        return None
    base_url = settings.classifier_base_url or DEFAULT_BASE_URLS[settings.classifier_provider]
    api_key = (
        settings.classifier_api_key.get_secret_value()
        if settings.classifier_api_key
        else settings.classifier_provider  # Ollama ignores the key but the SDK needs one.
    )
    client = AsyncOpenAI(
        base_url=base_url, api_key=api_key, timeout=settings.classifier_timeout, max_retries=1
    )
    effort = settings.classifier_reasoning_effort
    if effort is None and settings.classifier_provider == "ollama":
        effort = "none"
    return OpenAICompatClassifier(client, settings.classifier_model, reasoning_effort=effort)


# --- runtime settings ------------------------------------------------------------

# Keys in the app_setting table that override the matching Settings fields.
OVERRIDABLE = (
    "classifier_model",
    "default_agent",
    "job_roots",
    "vault_path",
    "vault_daily_folder",
    "notify_hermes_target",
    "weekly_review_weekday",
    "weekly_review_hour",
    "backup_keep",
)


def apply_overrides(settings: Settings, overrides: dict[str, Any]) -> Settings:
    """.env gives the defaults; what the user picked in the app wins."""
    update = {k: v for k, v in overrides.items() if k in OVERRIDABLE}
    if "job_roots" in update:  # stored as JSON strings
        update["job_roots"] = [Path(p) for p in update["job_roots"]]
    if update.get("vault_path"):
        update["vault_path"] = Path(update["vault_path"])
    return settings.model_copy(update=update) if update else settings


@dataclass(frozen=True)
class OllamaModel:
    name: str
    remote: bool  # "*-cloud" models run on ollama.com: text leaves this machine
    parameter_size: str | None


async def list_ollama_models(settings: Settings) -> list[OllamaModel]:
    """Chat-capable models installed in the local Ollama (embedding-only ones are left
    out). Raises ClassifierError when Ollama cannot be reached."""
    base = (settings.classifier_base_url or DEFAULT_BASE_URLS["ollama"]).removesuffix("/")
    root = base.removesuffix("/v1")
    try:
        async with httpx2.AsyncClient(timeout=5) as http:
            response = await http.get(f"{root}/api/tags")
            response.raise_for_status()
            data: dict[str, Any] = response.json()
    except Exception as exc:
        raise ClassifierError(f"cannot reach Ollama at {root}: {type(exc).__name__}") from exc
    models: list[OllamaModel] = []
    entries: list[dict[str, Any]] = data.get("models", [])
    for m in entries:
        capabilities: list[str] | None = m.get("capabilities")
        if capabilities is not None and "completion" not in capabilities:
            continue
        details: dict[str, Any] = m.get("details") or {}
        size: str | None = details.get("parameter_size") or None
        models.append(
            OllamaModel(name=str(m["name"]), remote=bool(m.get("remote_host")), parameter_size=size)
        )
    return sorted(models, key=lambda m: (m.remote, m.name))
