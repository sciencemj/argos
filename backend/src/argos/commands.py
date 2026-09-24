"""Deterministic slash-command parser (PLAN Phase 3: no LLM).

    /task <title> [date] [time]           date only → due 23:59
    /event <title> <date> [HH:MM[-HH:MM]]  no time → all-day
    /note <text>
    /ask <question>

Dates: 오늘, 내일, 모레, weekdays (금, 금요일, 금요일까지), 다음주 <weekday>, M/D,
YYYY-MM-DD. Times: HH:MM or N시. Date and time are read from the end of the text.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

WEEKDAYS = "월화수목금토일"
RELATIVE = {"오늘": 0, "내일": 1, "모레": 2}
DEFAULT_DUE = time(23, 59)


class CommandError(ValueError):
    """Message is shown to the user as-is (Korean)."""


@dataclass(frozen=True)
class TaskCommand:
    title: str
    due_at: datetime | None


@dataclass(frozen=True)
class EventCommand:
    title: str
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    all_day: date | None = None


@dataclass(frozen=True)
class NoteCommand:
    text: str


@dataclass(frozen=True)
class AskCommand:
    text: str


Command = TaskCommand | EventCommand | NoteCommand | AskCommand

_TIME = re.compile(r"^(\d{1,2}):(\d{2})$|^(\d{1,2})시$")
_RANGE = re.compile(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$")
_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_MD = re.compile(r"^(\d{1,2})/(\d{1,2})$")


def _time(token: str) -> time | None:
    m = _TIME.match(token)
    if not m:
        return None
    hour, minute = (int(m[1]), int(m[2])) if m[1] else (int(m[3]), 0)
    if hour > 23 or minute > 59:
        raise CommandError(f"시간을 읽을 수 없어요: {token}")
    return time(hour, minute)


def _date(token: str, today: date, next_week: bool) -> date | None:
    token = token.removesuffix("까지")
    if token in RELATIVE and not next_week:
        return today + timedelta(days=RELATIVE[token])
    day = token.removesuffix("요일")
    if len(day) == 1 and day in WEEKDAYS:
        target = WEEKDAYS.index(day)
        if next_week:
            monday = today - timedelta(days=today.weekday()) + timedelta(days=7)
            return monday + timedelta(days=target)
        return today + timedelta(days=(target - today.weekday()) % 7)
    if next_week:
        return None
    try:
        if m := _ISO.match(token):
            return date(int(m[1]), int(m[2]), int(m[3]))
        if m := _MD.match(token):
            candidate = date(today.year, int(m[1]), int(m[2]))
            # "1/5" typed in December means next January.
            return (
                candidate
                if candidate >= today - timedelta(days=30)
                else candidate.replace(year=today.year + 1)
            )
    except ValueError as exc:
        raise CommandError(f"없는 날짜예요: {token}") from exc
    return None


def _split_when(
    words: list[str], today: date
) -> tuple[list[str], date | None, time | None, tuple[time, time] | None]:
    """Peels a trailing [date] [time | time-range] off `words`."""
    words = list(words)
    at: time | None = None
    span: tuple[time, time] | None = None
    if words and (m := _RANGE.match(words[-1])):
        start, end = time(int(m[1]), int(m[2])), time(int(m[3]), int(m[4]))
        if end <= start:
            raise CommandError("끝 시간이 시작 시간보다 늦어야 해요")
        span = (start, end)
        words.pop()
    elif words and (parsed := _time(words[-1])) is not None:
        at = parsed
        words.pop()

    day: date | None = None
    if words:
        next_week = len(words) >= 2 and words[-2] == "다음주"
        day = _date(words[-1], today, next_week)
        if day is not None:
            words.pop()
            if next_week:
                words.pop()
    return words, day, at, span


def parse(text: str, now: datetime, tz: ZoneInfo) -> Command | None:
    """Returns None for plain text (it goes to the inbox classifier instead)."""
    text = text.strip()
    if not text.startswith("/"):
        return None
    name, _, rest = text.partition(" ")
    rest = rest.strip()
    today = now.astimezone(tz).date()

    if name in ("/note", "/ask"):
        if not rest:
            raise CommandError(f"{name} 뒤에 내용을 적어 주세요")
        return NoteCommand(rest) if name == "/note" else AskCommand(rest)

    if name not in ("/task", "/event"):
        raise CommandError(f"모르는 명령이에요: {name} (/task, /event, /note, /ask)")

    words, day, at, span = _split_when(rest.split(), today)
    title = " ".join(words)
    if not title:
        raise CommandError(f"{name} 뒤에 제목을 적어 주세요")

    if name == "/task":
        if span:
            raise CommandError("/task에는 시간 범위 대신 마감 시각 하나만 적어 주세요")
        if day is None and at is None:
            return TaskCommand(title, None)
        due_day = day or today
        return TaskCommand(title, datetime.combine(due_day, at or DEFAULT_DUE, tz))

    if day is None and at is None and span is None:
        raise CommandError("/event에는 날짜를 적어 주세요 (예: /event 퀴즈 9/30 10:30-11:15)")
    event_day = day or today
    if span:
        return EventCommand(
            title,
            starts_at=datetime.combine(event_day, span[0], tz),
            ends_at=datetime.combine(event_day, span[1], tz),
        )
    if at:
        start = datetime.combine(event_day, at, tz)
        return EventCommand(title, starts_at=start, ends_at=start + timedelta(hours=1))
    return EventCommand(title, all_day=event_day)


# --- date expressions inside free text (used to correct classifier output) --------

_PARTICLES = ("까지는", "까지", "부터", "에는", "에", "중으로", "중")
_CLOCK = re.compile(r"(오전|오후)?\s*(\d{1,2})시(?:\s*(\d{1,2})분|\s*(반))?|(\d{1,2}):(\d{2})")


def _bare(word: str) -> str:
    word = word.strip(".,!?~()[]\"'")
    for particle in _PARTICLES:
        if word.endswith(particle) and len(word) > len(particle):
            return word[: -len(particle)]
    return word


def find_date(text: str, today: date) -> date | None:
    """First date expression in free text ("다음주 수요일까지" → that Wednesday)."""
    words = [_bare(w) for w in text.split()]
    for i, word in enumerate(words):
        next_week = word == "다음주" and i + 1 < len(words)
        try:
            found = _date(words[i + 1], today, True) if next_week else _date(word, today, False)
        except CommandError:
            continue
        if found is not None:
            return found
    return None


def find_time(text: str) -> time | None:
    """First clock time in free text: 15:00, 3시, 오후 3시 30분, 오전 10시 반."""
    m = _CLOCK.search(text)
    if not m:
        return None
    if m[5]:
        hour, minute = int(m[5]), int(m[6])
    else:
        hour = int(m[2])
        minute = 30 if m[4] else int(m[3] or 0)
        if m[1] == "오후" and hour < 12:
            hour += 12
        elif m[1] == "오전" and hour == 12:
            hour = 0
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)
