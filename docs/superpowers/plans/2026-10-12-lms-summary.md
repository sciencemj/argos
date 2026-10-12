# LMS 알림 요약과 알림 카드 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** LMS 알림(과제 외)을 `lms_notice`로 구조화해 피드에서 카드로 보여 주고, 긴 알림은 분류기 설정의 채팅 모델이 백그라운드로 요약한다.

**Architecture:** `services.import_lms`가 메시지와 함께 `lms_notice` 행을 같은 트랜잭션에서 쓰고 요약할 notice id를 돌려준다. `summarizer.py`의 `LmsSummaries` 워커가 큐에서 하나씩 요약해 `services`의 `_update`로 저장한다(활동 기록 + WS). API는 `MessageOut.ref.lms_notice`로 내려 주고 프런트 `LmsNoticeCard`가 그린다.

**Tech Stack:** FastAPI, async SQLAlchemy(SQLite), Alembic, openai `AsyncOpenAI`(OpenAI 호환: Ollama/Hermes), React 19, TanStack Query, vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-10-12-lms-summary-design.md`

## Global Constraints

- 모든 쓰기는 `services.py`의 `_create`/`_update`(또는 같은 패턴의 `_log` + `_publish`)를 거친다. actor는 `system:lms`.
- UI 문구는 한국어(`tr("한국어", "English")`), 코드·식별자·커밋 메시지는 영어.
- 색은 `frontend/src/index.css` 토큰(CSS 변수)만. 학교별 문구를 코드에 박지 않는다.
- 외부 시스템(LLM)을 부르는 코드는 테스트용 fake와 함께. 테스트는 `tests/conftest.py`의 `settings`/`session`/`client` 픽스처.
- 모델 변경 → `uv run alembic revision --autogenerate` 후 `uv run alembic check`. API 모델 변경 → `make api-types` 후 커밋.
- `make lint`(ruff, pyright strict, biome, tsc)와 `make test`가 통과해야 한다.
- 요약 대상: 원문(공백 제거) 길이 ≥ `lms_summary_min_chars`(기본 300), 0이면 요약 끔. 원문은 앞 4,000자만 보낸다. 요약은 2~4개 `- ` 불릿.

## Spec에서 달라진 점 (구현 중 확정, Task 7에서 spec·decisions에 반영)

- 요약 길이 설정은 `OVERRIDABLE`이 아니라 "과목 이름 정리"와 같은 방식: `Settings.lms_summary_min_chars`(env 기본값) 위에 `app_setting` 키 `lms_summary_min_chars`, LMS 설정 엔드포인트 `PUT /api/v1/lms/summary`.
- 기존 알림 백필은 Alembic 데이터 마이그레이션이 아니라 서버 시작 때 `services.backfill_lms_notices`(멱등). 앱 코드로 테스트할 수 있다.
- `lms_key`가 해시라 기존 알림의 종류를 알 수 없다 → `kind`는 nullable, 백필 행은 `None`(카드 배지 "LMS").
- 요약 후 body는 `제목\n\n요약\n\nURL`(URL 유지: 에이전트 컨텍스트에 링크가 남게).

## Review Focus

1. 요약 중(pending)에 다음 동기화가 같은 항목의 바뀐 원문을 가져옴 → 늦게 도착한 옛 요약이 새 원문을 덮어쓰지 않아야 한다. (Task 4 `test_stale_summary_is_dropped`)
2. LLM이 불릿 없이 문단으로 답하거나 `<think>` 블록을 붙임 → think는 버리고, 불릿이 없으면 실패(원문 표시)로. (Task 2 `test_parse_bullets_*`)
3. 분류기가 설정되지 않은 사용자 → 모든 알림이 `skipped`로 원문 카드, 워커가 아무것도 부르지 않음. (Task 3 `test_import_without_summarizer_skips`)
4. 요약 실패 후 내용이 같은 항목이 다시 동기화됨 → 다시 `pending`으로 큐에 들어감. (Task 3 `test_failed_summary_retries_on_next_import`)
5. 원문에 `*`·`[링크](javascript:…)` 같은 Markdown/스크립트 → 카드는 텍스트로만 그리고 `javascript:` 링크를 만들지 않음. (Task 6 카드 테스트)

---

### Task 1: `lms_notice` 모델, 마이그레이션, 설정 기본값

**Files:**
- Modify: `backend/src/argos/models.py` (Message 클래스 아래, `Attachment` 위)
- Modify: `backend/src/argos/config.py` (`notify_digest_hour` 근처)
- Create: `backend/alembic/versions/<rev>_lms_notice.py` (autogenerate)
- Test: `backend/tests/test_services.py`

**Interfaces:**
- Produces: `LmsNotice(Record)` 컬럼 `message_id: str`, `kind: str | None`, `title: str`, `text: str`, `url: str`, `summary: str | None`, `summary_state: str`, `summary_hash: str | None`. `SummaryState = Literal["pending", "done", "skipped", "failed"]`. `Settings.lms_summary_min_chars: int = 300`.

- [ ] **Step 1: 실패하는 테스트**

`backend/tests/test_services.py` 끝에 추가:

```python
async def test_lms_notice_belongs_to_a_message(session: AsyncSession) -> None:
    from argos.models import LmsNotice, Message

    channel = await make_channel(session)
    message = Message(channel_id=channel.id, author_type=AuthorType.SYSTEM, body="공지")
    session.add(message)
    await session.flush()
    session.add(
        LmsNotice(
            message_id=message.id,
            kind="announcement",
            title="공지",
            text="본문",
            url="https://lms.example/1",
            summary_state="skipped",
        )
    )
    await session.commit()
    notice = await session.scalar(select(LmsNotice).where(LmsNotice.message_id == message.id))
    assert notice is not None and notice.summary is None
```

(`AuthorType`가 이 파일에 import되어 있지 않으면 `from argos.models import AuthorType`를 맨 위 import에 추가.)

- [ ] **Step 2: 실패 확인**

Run: `cd backend && uv run pytest tests/test_services.py::test_lms_notice_belongs_to_a_message -q`
Expected: FAIL `ImportError: cannot import name 'LmsNotice'`

- [ ] **Step 3: 모델과 설정**

`models.py`의 `class Message` 정의 바로 뒤:

```python
SummaryState = Literal["pending", "done", "skipped", "failed"]


class LmsNotice(Record):
    """An LMS notice behind a system message (PLAN Phase 15a): the original text the
    card shows under "원문 보기" and the AI summary once it is written."""

    __tablename__ = "lms_notice"

    message_id: Mapped[str] = mapped_column(
        ForeignKey("message.id", ondelete="CASCADE"), unique=True
    )
    kind: Mapped[str | None] = mapped_column(String(20))  # None: imported before 15a
    title: Mapped[str] = mapped_column(String(500))
    text: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(String(2000))
    summary: Mapped[str | None] = mapped_column(Text)
    summary_state: Mapped[str] = mapped_column(String(10))  # SummaryState
    summary_hash: Mapped[str | None] = mapped_column(String(64))  # sha256 of summarized text
```

`Literal`이 models.py에 import되어 있지 않으면 `from typing import Literal` 추가.

`config.py`의 `notify_digest_hour` 줄 아래:

```python
    # LMS notices at least this long (characters) get an AI summary; 0 turns it off.
    lms_summary_min_chars: int = 300
```

- [ ] **Step 4: 마이그레이션**

Run:
```bash
cd backend && uv run alembic revision --autogenerate -m "lms notice" && uv run alembic upgrade head && uv run alembic check
```
Expected: 새 파일에 `op.create_table("lms_notice", ...)`와 unique 제약, `alembic check`가 "No new upgrade operations detected".
(개발 서버가 launchd로 떠 있으면 `upgrade head`가 서버 리로드 전에 적용되어야 한다 — CLAUDE.md.)

- [ ] **Step 5: 통과 확인**

Run: `cd backend && uv run pytest tests/test_services.py::test_lms_notice_belongs_to_a_message -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/argos/models.py backend/src/argos/config.py backend/alembic/versions backend/tests/test_services.py
git commit -m "feat: store LMS notices behind system messages"
```

---

### Task 2: 요약기 (`summarizer.py` 앞부분)

**Files:**
- Create: `backend/src/argos/summarizer.py`
- Modify: `backend/tests/fakes.py`
- Test: `backend/tests/test_summarizer.py`

**Interfaces:**
- Consumes: `Settings.classifier_model`, `classifier_title_model`, `classifier_provider`, `classifier_base_url`, `classifier_api_key`, `classifier_timeout`, `language`; `classifier.DEFAULT_BASE_URLS`.
- Produces:
  - `class SummaryError(Exception)`
  - `class Summarizer(Protocol): async def summarize(self, title: str, text: str, language: str) -> str`
  - `def parse_bullets(content: str) -> str` (줄바꿈으로 이은 `- ` 줄들, 실패 시 `SummaryError`)
  - `class OpenAISummarizer(client: AsyncOpenAI, model: str)`
  - `def build_summarizer(settings: Settings) -> Summarizer | None`
  - `MAX_SOURCE_CHARS = 4000`
  - `tests/fakes.py`: `class FakeSummarizer(answer: str = "- 요약", error: bool = False)` with `.calls: list[tuple[str, str]]`

- [ ] **Step 1: 실패하는 테스트**

`backend/tests/test_summarizer.py`:

```python
import json
from typing import Any

import httpx2 as httpx
import pytest
from openai import AsyncOpenAI

from argos.config import Settings
from argos.summarizer import (
    MAX_SOURCE_CHARS,
    OpenAISummarizer,
    SummaryError,
    build_summarizer,
    parse_bullets,
)


def completion(content: str) -> dict[str, Any]:
    return {
        "id": "x",
        "object": "chat.completion",
        "created": 0,
        "model": "m",
        "choices": [
            {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}
        ],
    }


def summarizer(handler: Any) -> OpenAISummarizer:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AsyncOpenAI(base_url="http://llm.test/v1", api_key="k", http_client=http, max_retries=0)
    return OpenAISummarizer(client, "m")


def test_parse_bullets_normalizes_markers_and_caps_at_four() -> None:
    content = "요약입니다\n* 하나\n• 둘\n- 셋\n- 넷\n- 다섯"
    assert parse_bullets(content) == "- 하나\n- 둘\n- 셋\n- 넷"


def test_parse_bullets_drops_think_blocks() -> None:
    assert parse_bullets("<think>- 생각</think>\n- 마감 10/17") == "- 마감 10/17"


def test_parse_bullets_without_bullets_fails() -> None:
    with pytest.raises(SummaryError):
        parse_bullets("그냥 문단으로 답했어요.")


async def test_summarize_sends_truncated_text_and_language() -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=completion("- 숙제 2 공개\n- 마감 10/17 23:59"))

    result = await summarizer(handler).summarize("Homework 2", "x" * 5000, "ko")
    assert result == "- 숙제 2 공개\n- 마감 10/17 23:59"
    user = requests[0]["messages"][-1]["content"]
    assert user.startswith("Homework 2\n\n")
    assert user.count("x") == MAX_SOURCE_CHARS
    assert "한국어" in requests[0]["messages"][0]["content"]


async def test_request_failure_is_a_summary_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "down"})

    with pytest.raises(SummaryError):
        await summarizer(handler).summarize("t", "본문", "ko")


def test_build_summarizer_prefers_the_title_model(settings: Settings) -> None:
    assert build_summarizer(settings) is None
    chat = settings.model_copy(update={"classifier_model": "decide", "classifier_title_model": "writer"})
    built = build_summarizer(chat)
    assert isinstance(built, OpenAISummarizer) and built.model == "writer"
    plain = settings.model_copy(update={"classifier_model": "qwen"})
    built = build_summarizer(plain)
    assert isinstance(built, OpenAISummarizer) and built.model == "qwen"
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && uv run pytest tests/test_summarizer.py -q`
Expected: FAIL `ModuleNotFoundError: No module named 'argos.summarizer'`

- [ ] **Step 3: 구현**

`backend/src/argos/summarizer.py`:

```python
"""AI summaries of LMS notices (PLAN Phase 15a), written with the Inbox classifier's
chat model. A notice without a summary still shows its original text."""

import logging
import re
from typing import Protocol

from openai import AsyncOpenAI

from argos.classifier import DEFAULT_BASE_URLS
from argos.config import Settings

log = logging.getLogger(__name__)

MAX_SOURCE_CHARS = 4000  # local models have small context windows
MAX_BULLETS = 4

PROMPT = {
    "ko": (
        "학교 LMS에서 온 알림이다. 학생이 알아야 할 것만 2~4개의 '- ' 불릿으로 한국어로 써라. "
        "날짜·마감·장소·준비물·변경 사항은 빠뜨리지 마라. 수신 안내, 알림 설정·구독 해지 문구, "
        "URL, 과목 코드는 쓰지 마라. 원문에 없는 내용은 쓰지 마라. 불릿 말고 다른 줄은 쓰지 마라."
    ),
    "en": (
        "This is a notice from a school LMS. Write only what the student needs to know as 2-4 "
        "'- ' bullets in English. Keep every date, deadline, place, item to bring and change. "
        "Leave out why-you-got-this lines, notification or unsubscribe text, URLs and course "
        "codes. Add nothing that is not in the notice. Write nothing but the bullets."
    ),
}


class SummaryError(Exception):
    pass


class Summarizer(Protocol):
    async def summarize(self, title: str, text: str, language: str) -> str: ...


def parse_bullets(content: str) -> str:
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.S)
    bullets = [
        "- " + match.group(1).strip()
        for line in content.splitlines()
        if (match := re.match(r"\s*[-*•]\s+(.+)", line))
    ]
    if not bullets:
        raise SummaryError("no bullets in the summary")
    return "\n".join(bullets[:MAX_BULLETS])


class OpenAISummarizer:
    def __init__(self, client: AsyncOpenAI, model: str) -> None:
        self._client = client
        self.model = model

    async def summarize(self, title: str, text: str, language: str) -> str:
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": PROMPT.get(language, PROMPT["ko"])},
                    {"role": "user", "content": f"{title}\n\n{text[:MAX_SOURCE_CHARS]}"},
                ],
                temperature=0,
                extra_body={"reasoning_effort": "none"},
            )
            content = response.choices[0].message.content or ""
        except Exception as exc:
            raise SummaryError(f"summary request failed: {type(exc).__name__}") from exc
        return parse_bullets(content)


def build_summarizer(settings: Settings) -> Summarizer | None:
    """The classifier's chat model: the title writer when the classifier is a
    decision-only model. None when no classifier is set up."""
    model = settings.classifier_title_model or settings.classifier_model
    if not model:
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
    return OpenAISummarizer(client, model)
```

`backend/tests/fakes.py` 끝에:

```python
class FakeSummarizer:
    """Answers a fixed summary (or fails) and remembers (title, text) it was given."""

    def __init__(self, answer: str = "- 요약", error: bool = False) -> None:
        self.answer = answer
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def summarize(self, title: str, text: str, language: str) -> str:
        from argos.summarizer import SummaryError

        self.calls.append((title, text))
        if self.error:
            raise SummaryError("fake failure")
        return self.answer
```

- [ ] **Step 4: 통과 확인**

Run: `cd backend && uv run pytest tests/test_summarizer.py -q && uv run pyright src/argos/summarizer.py tests/test_summarizer.py`
Expected: 6 passed, 0 errors

- [ ] **Step 5: Commit**

```bash
git add backend/src/argos/summarizer.py backend/tests/fakes.py backend/tests/test_summarizer.py
git commit -m "feat: summarize LMS notices with the classifier's chat model"
```

---

### Task 3: `import_lms`가 notice를 쓰고 요약 대상을 고른다 + 저장 헬퍼 + 백필

**Files:**
- Modify: `backend/src/argos/services.py` (`import_lms` 341행 부근과 그 아래 새 함수들)
- Test: `backend/tests/test_lms_notice.py` (새 파일)

**Interfaces:**
- Consumes: `LmsNotice`, `SummaryState` (Task 1)
- Produces (모두 `services`):
  - `import_lms(session, *, account_id, courses, items, summary_min_chars: int = 0) -> dict[str, Any]` — 기존 `created/updated/unchanged` 키 + `"summarize": list[str]`(pending notice id)
  - `def lms_body(title: str, text: str, url: str, summary: str | None = None) -> str`
  - `def summary_state(text: str, min_chars: int) -> SummaryState`
  - `async def pending_summaries(session) -> list[str]`
  - `async def save_summary(session, notice_id: str, text: str, summary: str) -> bool` — `text`가 notice의 현재 원문과 다르거나 상태가 pending이 아니면 False(버림)
  - `async def fail_summary(session, notice_id: str, text: str) -> None`
  - `def split_lms_body(body: str) -> tuple[str, str, str] | None`
  - `async def backfill_lms_notices(session) -> int`
  - `async def lms_summary_min_chars(session, default: int) -> int`, `async def set_lms_summary_min_chars(session, value: int, actor: str) -> None`

- [ ] **Step 1: 실패하는 테스트**

`backend/tests/test_lms_notice.py`:

```python
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from argos import services
from argos.models import AuthorType, LmsNotice, Message, SourceLink

LONG = "중간고사 범위는 3장부터 7장까지입니다. " * 20  # > 300 chars


def items(text: str = LONG, kind: str = "announcement", item_id: str = "11") -> list[dict[str, Any]]:
    return [
        {
            "id": item_id,
            "course_id": "7",
            "kind": kind,
            "title": "중간고사 *안내*",
            "text": text,
            "url": "https://lms.example/courses/7/announcements/11",
            "due_at": None,
            "file": None,
        }
    ]


async def run_import(session: AsyncSession, text: str = LONG, min_chars: int = 300) -> dict[str, Any]:
    return await services.import_lms(
        session,
        account_id="42",
        courses=[{"id": "7", "name": "알고리즘"}],
        items=items(text),
        summary_min_chars=min_chars,
    )


async def notice(session: AsyncSession) -> LmsNotice:
    found = await session.scalar(select(LmsNotice))
    assert found is not None
    await session.refresh(found)
    return found


async def test_import_creates_pending_notice_for_long_text(session: AsyncSession) -> None:
    result = await run_import(session)
    row = await notice(session)
    assert result["summarize"] == [row.id]
    assert (row.kind, row.title, row.summary_state) == ("announcement", "중간고사 *안내*", "pending")
    assert row.text == LONG and row.url.endswith("/11")


async def test_short_text_is_skipped(session: AsyncSession) -> None:
    result = await run_import(session, text="출석 처리되었습니다")
    assert result["summarize"] == []
    assert (await notice(session)).summary_state == "skipped"


async def test_import_without_summarizer_skips(session: AsyncSession) -> None:
    result = await run_import(session, min_chars=0)
    assert result["summarize"] == []
    assert (await notice(session)).summary_state == "skipped"


async def test_changed_text_goes_pending_again_and_clears_summary(session: AsyncSession) -> None:
    await run_import(session)
    row = await notice(session)
    assert await services.save_summary(session, row.id, LONG, "- 3~7장")
    result = await run_import(session, text=LONG + " 장소: 101호")
    row = await notice(session)
    assert result["summarize"] == [row.id]
    assert (row.summary_state, row.summary) == ("pending", None)


async def test_failed_summary_retries_on_next_import(session: AsyncSession) -> None:
    await run_import(session)
    row = await notice(session)
    await services.fail_summary(session, row.id, LONG)
    assert (await notice(session)).summary_state == "failed"
    result = await run_import(session)  # same content
    assert result["summarize"] == [row.id]
    assert (await notice(session)).summary_state == "pending"


async def test_save_summary_updates_body_and_drops_stale_text(session: AsyncSession) -> None:
    await run_import(session)
    row = await notice(session)
    assert not await services.save_summary(session, row.id, "다른 원문", "- 옛 요약")
    assert await services.save_summary(session, row.id, LONG, "- 3~7장")
    row = await notice(session)
    assert (row.summary_state, row.summary) == ("done", "- 3~7장")
    message = await session.get(Message, row.message_id)
    assert message is not None
    await session.refresh(message)
    assert message.body == "중간고사 \\*안내\\*\n\n- 3~7장\n\nhttps://lms.example/courses/7/announcements/11"


async def test_pending_summaries_lists_waiting_notices(session: AsyncSession) -> None:
    await run_import(session)
    row = await notice(session)
    assert await services.pending_summaries(session) == [row.id]


def test_split_lms_body_unescapes_markdown() -> None:
    body = services.lms_body("공지 *1*", "본문 [링크]\n\n둘째 문단", "https://lms.example/1")
    assert services.split_lms_body(body) == ("공지 *1*", "본문 [링크]\n\n둘째 문단", "https://lms.example/1")
    assert services.split_lms_body("사용자가 쓴 메시지") is None


async def test_backfill_creates_skipped_notices_for_old_messages(session: AsyncSession) -> None:
    await run_import(session)
    old = await notice(session)
    await session.delete(old)  # as if imported before Phase 15a
    await session.commit()
    assert await services.backfill_lms_notices(session) == 1
    row = await notice(session)
    assert (row.kind, row.summary_state, row.title) == (None, "skipped", "중간고사 *안내*")
    assert await services.backfill_lms_notices(session) == 0


async def test_summary_min_chars_setting(session: AsyncSession) -> None:
    assert await services.lms_summary_min_chars(session, 300) == 300
    await services.set_lms_summary_min_chars(session, 0, "user")
    assert await services.lms_summary_min_chars(session, 300) == 0
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && uv run pytest tests/test_lms_notice.py -q`
Expected: FAIL (`unexpected keyword argument 'summary_min_chars'` 등)

- [ ] **Step 3: 구현 — 헬퍼**

`services.py`의 `lms_hash` 아래에 추가(`LmsNotice`, `SummaryState`를 models import 목록에 추가):

```python
LMS_ESCAPE = r"([\\`*_{}\[\]<>#|!~])"


def lms_body(title: str, text: str, url: str, summary: str | None = None) -> str:
    """The message body behind an LMS card: what search, agents and MCP read.
    Markdown punctuation is escaped so LMS text stays text in Argos' renderer."""
    middle = summary if summary is not None else re.sub(LMS_ESCAPE, r"\\\1", text)
    return f"{re.sub(LMS_ESCAPE, r'\\\1', title)}\n\n{middle}\n\n{url}"


def split_lms_body(body: str) -> tuple[str, str, str] | None:
    """Undo lms_body (without a summary) for messages imported before lms_notice."""
    parts = body.split("\n\n")
    if len(parts) < 3 or not parts[-1].startswith(("http://", "https://")):
        return None

    def plain(value: str) -> str:
        return re.sub(r"\\" + LMS_ESCAPE, r"\1", value)

    return plain(parts[0]), plain("\n\n".join(parts[1:-1])), parts[-1]


def summary_state(text: str, min_chars: int) -> SummaryState:
    return "pending" if min_chars > 0 and len(text.strip()) >= min_chars else "skipped"


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def lms_summary_min_chars(session: AsyncSession, default: int) -> int:
    value = await get_setting(session, "lms_summary_min_chars")
    return default if value is None else int(value)


async def set_lms_summary_min_chars(session: AsyncSession, value: int, actor: str) -> None:
    if value < 0:
        raise InvalidError("요약 기준 글자 수는 0 이상이어야 해요")
    await set_setting(session, "lms_summary_min_chars", value, actor)


async def pending_summaries(session: AsyncSession) -> list[str]:
    return list(
        (
            await session.scalars(
                select(LmsNotice.id)
                .where(LmsNotice.summary_state == "pending")
                .order_by(LmsNotice.created_at)
            )
        ).all()
    )


async def save_summary(session: AsyncSession, notice_id: str, text: str, summary: str) -> bool:
    """False when the notice changed while it was being summarized: that summary is stale."""
    notice = await session.get(LmsNotice, notice_id)
    if notice is None:
        return False
    await session.refresh(notice)
    if notice.summary_state != "pending" or notice.text != text:
        return False
    message = await _get(session, Message, notice.message_id)
    await _update(
        session, message, {"body": lms_body(notice.title, text, notice.url, summary)}, "system:lms"
    )
    await _update(
        session,
        notice,
        {"summary": summary, "summary_state": "done", "summary_hash": _text_hash(text)},
        "system:lms",
    )
    return True


async def fail_summary(session: AsyncSession, notice_id: str, text: str) -> None:
    notice = await session.get(LmsNotice, notice_id)
    if notice is None:
        return
    await session.refresh(notice)
    if notice.summary_state == "pending" and notice.text == text:
        await _update(session, notice, {"summary_state": "failed"}, "system:lms")


async def backfill_lms_notices(session: AsyncSession) -> int:
    """Cards for LMS messages imported before Phase 15a. Idempotent; never summarizes."""
    rows = (
        await session.execute(
            select(Message, SourceLink)
            .join(
                SourceLink,
                (SourceLink.object_id == Message.id)
                & (SourceLink.source == "learningx")
                & (SourceLink.object_type == "message"),
            )
            .outerjoin(LmsNotice, LmsNotice.message_id == Message.id)
            .where(LmsNotice.id.is_(None))
        )
    ).all()
    made = 0
    for message, _link in rows:
        if (parts := split_lms_body(message.body)) is None:
            continue
        title, text, url = parts
        session.add(
            LmsNotice(
                message_id=message.id,
                kind=None,
                title=title[:500],
                text=text,
                url=url[:2000],
                summary_state="skipped",
            )
        )
        made += 1
    await session.commit()
    return made
```

(백필은 화면에 이미 있는 메시지의 표시용 행이라 활동 기록·WS 이벤트를 남기지 않는다. 서버 시작 때 한 번 돌고 첫 조회부터 카드가 보인다.)

- [ ] **Step 4: 구현 — `import_lms`**

시그니처에 `summary_min_chars: int = 0`을 추가하고 반환 타입을 `dict[str, Any]`로 바꾼다. 맨 위에 `summarize: list[str] = []`. 기존 본문 조립 세 줄(`plain = …`, `title = …`, `body = …`)을 다음으로 바꾼다:

```python
        body = lms_body(item["title"], item["text"], item["url"])
```

`for item in items:` 루프의 맨 끝(`link.container = item["url"]` 다음 줄)에 추가:

```python
        if isinstance(obj, Message):
            if notice_id := await _sync_lms_notice(
                session, obj, item, summary_min_chars, changes, record
            ):
                summarize.append(notice_id)
```

`return counts`를 `return {**counts, "summarize": summarize}`로.

`import_lms` 위에 헬퍼:

```python
async def _sync_lms_notice(
    session: AsyncSession,
    message: Message,
    item: dict[str, Any],
    min_chars: int,
    changes: list[tuple[str, Record, dict[str, Any]]],
    record: Callable[[Record], Awaitable[None]],
) -> str | None:
    """Keeps the card row in step with the LMS item; returns its id when it needs a
    summary. A failed summary is tried again whenever the item comes back."""
    fields = {
        "kind": item["kind"],
        "title": item["title"][:500],
        "text": item["text"],
        "url": item["url"],
    }
    state = summary_state(item["text"], min_chars)
    notice = await session.scalar(select(LmsNotice).where(LmsNotice.message_id == message.id))
    if notice is None:
        notice = LmsNotice(message_id=message.id, summary_state=state, **fields)
        await record(notice)
        return notice.id if state == "pending" else None
    before = snapshot(notice)
    if notice.text != item["text"]:
        update: dict[str, Any] = {
            **fields, "summary": None, "summary_hash": None, "summary_state": state
        }
    elif notice.summary_state == "failed" and state == "pending":
        update = {**fields, "summary_state": "pending"}
    else:
        update = fields
    for key, value in update.items():
        setattr(notice, key, value)
    await session.flush()
    after = snapshot(notice)
    diff = {k for k in after if after[k] != before[k] and k != "updated_at"}
    if diff:
        _log(
            session, notice, "updated", "system:lms",
            before={k: before[k] for k in diff}, after={k: after[k] for k in diff},
        )
        changes.append(("object.updated", notice, after))
    # Back in the queue when it newly waits (failed → pending) or its text changed.
    requeue = notice.summary_state == "pending" and ("summary_state" in diff or "text" in diff)
    return notice.id if requeue else None
```

`Callable`, `Awaitable`을 `collections.abc`에서 import. 기존 메시지 갱신 경로에서 `fields = {"body": body}`이므로 원문이 바뀌면 body는 요약 없는 새 원문으로 돌아간다(요약은 워커가 다시 씀).

- [ ] **Step 5: 통과 확인**

Run: `cd backend && uv run pytest tests/test_lms_notice.py tests/test_lms.py -q && uv run pyright src/argos/services.py`
Expected: 모두 PASS(`test_import_logs_and_plain_text`의 `\[링크\]` 검사 포함), 0 errors

- [ ] **Step 6: Commit**

```bash
git add backend/src/argos/services.py backend/tests/test_lms_notice.py
git commit -m "feat: keep an lms_notice row per LMS message and pick notices to summarize"
```

---

### Task 4: 요약 워커와 앱 연결

**Files:**
- Modify: `backend/src/argos/summarizer.py` (끝에 워커)
- Modify: `backend/src/argos/main.py` (lifespan)
- Modify: `backend/src/argos/lms.py` (`import_data`)
- Modify: `backend/src/argos/api.py:2401` (분류기 재구성 옆)
- Test: `backend/tests/test_summarizer.py`, `backend/tests/test_lms.py`

**Interfaces:**
- Consumes: `services.pending_summaries/save_summary/fail_summary/lms_summary_min_chars/backfill_lms_notices`, `build_summarizer`, `FakeSummarizer`
- Produces: `class LmsSummaries(sessionmaker, summarizer: Callable[[], Summarizer | None], settings: Callable[[], Settings])` with `available() -> bool`, `enqueue(ids: Iterable[str]) -> None`, `async process(notice_id: str) -> None`, `async start() -> None`, `async stop() -> None`. `app.state.summarizer`, `app.state.lms_summaries`.

- [ ] **Step 1: 실패하는 테스트**

`backend/tests/test_summarizer.py` 끝에:

```python
from sqlalchemy import select

from argos import services
from argos.db import make_engine, make_sessionmaker
from argos.models import LmsNotice
from argos.summarizer import LmsSummaries
from tests.fakes import FakeSummarizer

LONG = "기말 프로젝트 발표 순서와 준비물을 안내합니다. " * 20


async def seeded(settings: Settings, text: str = LONG) -> tuple[Any, str]:
    maker = make_sessionmaker(make_engine(settings.db_url))
    async with maker() as session:
        result = await services.import_lms(
            session,
            account_id="42",
            courses=[{"id": "7", "name": "알고리즘"}],
            items=[{"id": "1", "course_id": "7", "kind": "announcement", "title": "발표",
                    "text": text, "url": "https://lms.example/1", "due_at": None, "file": None}],
            summary_min_chars=300,
        )
    return maker, result["summarize"][0]


async def state(maker: Any, notice_id: str) -> LmsNotice:
    async with maker() as session:
        row = await session.get(LmsNotice, notice_id)
        assert row is not None
        return row


async def test_worker_saves_summary(settings: Settings) -> None:
    maker, notice_id = await seeded(settings)
    fake = FakeSummarizer("- 발표 순서 공개")
    await LmsSummaries(maker, lambda: fake, lambda: settings).process(notice_id)
    row = await state(maker, notice_id)
    assert (row.summary_state, row.summary) == ("done", "- 발표 순서 공개")
    assert fake.calls == [("발표", LONG)]


async def test_worker_failure_marks_failed(settings: Settings) -> None:
    maker, notice_id = await seeded(settings)
    await LmsSummaries(maker, lambda: FakeSummarizer(error=True), lambda: settings).process(notice_id)
    assert (await state(maker, notice_id)).summary_state == "failed"


async def test_stale_summary_is_dropped(settings: Settings) -> None:
    maker, notice_id = await seeded(settings)

    class ChangesWhileSummarizing(FakeSummarizer):
        async def summarize(self, title: str, text: str, language: str) -> str:
            async with maker() as session:
                row = await session.get(LmsNotice, notice_id)
                assert row is not None
                row.text = LONG + " 변경"
                await session.commit()
            return "- 옛 요약"

    await LmsSummaries(maker, lambda: ChangesWhileSummarizing(), lambda: settings).process(notice_id)
    row = await state(maker, notice_id)
    assert (row.summary_state, row.summary) == ("pending", None)


async def test_start_requeues_pending_and_processes(settings: Settings) -> None:
    maker, notice_id = await seeded(settings)
    worker = LmsSummaries(maker, lambda: FakeSummarizer("- 완료"), lambda: settings)
    await worker.start()
    await worker.join()
    await worker.stop()
    assert (await state(maker, notice_id)).summary_state == "done"
```

`backend/tests/test_lms.py` 끝에:

```python
def test_import_queues_long_notices_for_summary(client: TestClient) -> None:
    from tests.fakes import FakeSummarizer

    app = cast(FastAPI, client.app)
    fake = FakeSummarizer("- 요약됨")
    app.state.summarizer = fake
    payload = batch()
    payload["items"][1]["text"] = "시험 안내입니다. " * 40
    headers = {"Authorization": f"Bearer {token(client)}"}
    response = client.post("/api/v1/lms/import", json=payload, headers=headers)
    assert response.status_code == 200
    assert "summarize" not in response.json()
    # The worker runs on the app's event loop: wait by polling, not by joining its queue.
    deadline = time.time() + 5
    while not fake.calls and time.time() < deadline:
        time.sleep(0.05)
    assert len(fake.calls) == 1
```

(`import time`을 test_lms.py 맨 위에 추가. `LmsSummaries.join()`도 Produces에 포함: 같은 이벤트 루프의 테스트용으로 큐가 빌 때까지 기다림.)

- [ ] **Step 2: 실패 확인**

Run: `cd backend && uv run pytest tests/test_summarizer.py tests/test_lms.py::test_import_queues_long_notices_for_summary -q`
Expected: FAIL `ImportError: cannot import name 'LmsSummaries'`

- [ ] **Step 3: 워커 구현**

`summarizer.py`에 import 추가(`asyncio`, `from collections.abc import Callable, Iterable`, `from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker`, `from argos import services`, `from argos.models import LmsNotice`) 후 끝에:

```python
class LmsSummaries:
    """One notice at a time in the background: a slow local model must not hold up the
    LMS import, and it gets one request at a time."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        summarizer: Callable[[], Summarizer | None],
        settings: Callable[[], Settings],
    ) -> None:
        self._sessionmaker = sessionmaker
        self._summarizer = summarizer
        self._settings = settings
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._loop: asyncio.Task[None] | None = None

    def available(self) -> bool:
        return self._summarizer() is not None

    def enqueue(self, ids: Iterable[str]) -> None:
        for notice_id in ids:
            self._queue.put_nowait(notice_id)

    async def process(self, notice_id: str) -> None:
        summarizer = self._summarizer()
        async with self._sessionmaker() as session:
            notice = await session.get(LmsNotice, notice_id)
            if notice is None or notice.summary_state != "pending":
                return
            title, text = notice.title, notice.text
            if summarizer is None:
                await services.fail_summary(session, notice_id, text)
                return
            try:
                summary = await summarizer.summarize(title, text, self._settings().language)
            except SummaryError as exc:
                log.warning("LMS summary failed: %s", exc)
                await services.fail_summary(session, notice_id, text)
                return
            await services.save_summary(session, notice_id, text, summary)

    async def start(self) -> None:
        async with self._sessionmaker() as session:
            await services.backfill_lms_notices(session)
            self.enqueue(await services.pending_summaries(session))

        async def loop() -> None:
            while True:
                notice_id = await self._queue.get()
                try:
                    await self.process(notice_id)
                except Exception:
                    log.exception("LMS summary pass failed")
                finally:
                    self._queue.task_done()

        self._loop = asyncio.create_task(loop())

    async def join(self) -> None:
        await self._queue.join()

    async def stop(self) -> None:
        if self._loop is not None:
            self._loop.cancel()
            try:
                await self._loop
            except asyncio.CancelledError:
                pass
```

순환 import 확인: `services`가 `summarizer`를 import하지 않으므로 안전.

- [ ] **Step 4: 앱 연결**

`main.py` lifespan, `app.state.classifier = build_classifier(...)` 다음 줄:

```python
        app.state.summarizer = build_summarizer(app.state.settings)
```

`app.state.notifier.start()` 다음:

```python
        app.state.lms_summaries = LmsSummaries(
            app.state.sessionmaker, lambda: app.state.summarizer, lambda: app.state.settings
        )
        await app.state.lms_summaries.start()
```

종료 부분 `await app.state.notifier.stop()` 다음: `await app.state.lms_summaries.stop()`. import: `from argos.summarizer import LmsSummaries, build_summarizer`.

`api.py:2401` `request.app.state.classifier = classifier.build_classifier(...)` 다음 줄:

```python
    request.app.state.summarizer = build_summarizer(request.app.state.settings)
```

(`from argos.summarizer import build_summarizer` 추가.)

`lms.py`의 `import_data`를 다음으로 바꾼다(`Request`를 fastapi import에, `Config` 의존성 사용):

```python
@router.post("/import")
async def import_data(
    body: LmsImport,
    request: Request,
    session: Session,
    config: Config,
    authorization: Annotated[str | None, Header()] = None,
) -> LmsImportResult:
    summaries: LmsSummaries = request.app.state.lms_summaries
    async with sync_lock:
        # Check after acquiring the write lock so rotation revokes queued imports too.
        await paired(session, authorization)
        min_chars = (
            await services.lms_summary_min_chars(session, config.lms_summary_min_chars)
            if summaries.available()
            else 0
        )
        result = await services.import_lms(
            session,
            account_id=body.account_id,
            courses=[course.model_dump() for course in body.courses],
            items=[item.model_dump() for item in body.items],
            summary_min_chars=min_chars,
        )
    summaries.enqueue(result.pop("summarize"))
    return LmsImportResult(**result)
```

- [ ] **Step 5: 통과 확인**

Run: `cd backend && uv run pytest tests/test_summarizer.py tests/test_lms.py tests/test_lms_notice.py -q && uv run pyright`
Expected: 모두 PASS, 0 errors

- [ ] **Step 6: Commit**

```bash
git add backend/src/argos/summarizer.py backend/src/argos/main.py backend/src/argos/lms.py backend/src/argos/api.py backend/tests/test_summarizer.py backend/tests/test_lms.py
git commit -m "feat: summarize imported LMS notices in the background"
```

---

### Task 5: API — `ref.lms_notice`와 요약 설정 엔드포인트

**Files:**
- Modify: `backend/src/argos/api.py` (`RefOut` 395행, `_messages_out` 715행)
- Modify: `backend/src/argos/lms.py` (`LmsStatus`, `status`, 새 `PUT /summary`)
- Modify: `frontend/src/api-types.ts` (생성)
- Test: `backend/tests/test_lms.py`

**Interfaces:**
- Produces: `LmsNoticeOut(Out)`: `kind: str | None, title: str, text: str, url: str, summary: str | None, summary_state: Literal["pending","done","skipped","failed"]`. `RefOut.lms_notice: LmsNoticeOut | None`. `LmsStatus.summary_min_chars: int`, `LmsStatus.summary_available: bool`. `PUT /api/v1/lms/summary` body `LmsSummarySetting{min_chars: int (ge=0, le=20000)}` → `LmsStatus`.

- [ ] **Step 1: 실패하는 테스트**

`backend/tests/test_lms.py` 끝에:

```python
def test_messages_carry_the_lms_notice_card(client: TestClient) -> None:
    headers = {"Authorization": f"Bearer {token(client)}"}
    client.post("/api/v1/lms/import", json=batch(), headers=headers)
    channels = client.get("/api/v1/channels").json()
    course = next(c for c in channels if c["name"] == "알고리즘")
    items = client.get(f"/api/v1/channels/{course['id']}/messages").json()["items"]
    cards = [m["ref"]["lms_notice"] for m in items if m["ref"].get("lms_notice")]
    assert cards == [
        {
            "kind": "announcement",
            "title": "공지",
            "text": "[링크](javascript:alert(1))",
            "url": "https://lms.korea.ac.kr/courses/7/discussion_topics/11",
            "summary": None,
            "summary_state": "skipped",
        }
    ]


def test_summary_setting_round_trip(client: TestClient) -> None:
    status = client.get("/api/v1/lms/status").json()
    assert (status["summary_min_chars"], status["summary_available"]) == (300, False)
    status = client.put("/api/v1/lms/summary", json={"min_chars": 0}).json()
    assert status["summary_min_chars"] == 0
    assert client.put("/api/v1/lms/summary", json={"min_chars": -1}).status_code == 422
```

(`/api/v1/channels` 응답 형태가 다르면 `tests/test_api.py`에서 채널 목록을 읽는 방식을 따라 맞춘다.)

- [ ] **Step 2: 실패 확인**

Run: `cd backend && uv run pytest tests/test_lms.py -k "lms_notice_card or summary_setting" -q`
Expected: FAIL (`KeyError: 'lms_notice'`, `summary_min_chars`)

- [ ] **Step 3: 구현**

`api.py`, `RefOut` 위:

```python
class LmsNoticeOut(Out):
    kind: str | None
    title: str
    text: str
    url: str
    summary: str | None
    summary_state: Literal["pending", "done", "skipped", "failed"]
```

`RefOut`에 `lms_notice: LmsNoticeOut | None = None` 추가.

`_messages_out`에서 `files = …` 다음 줄:

```python
    notices = {
        n.message_id: LmsNoticeOut.model_validate(n)
        for n in (
            await session.scalars(
                select(LmsNotice).where(LmsNotice.message_id.in_([m.id for m in messages]))
            )
        ).all()
    }
```

루프 안 `item.ref = …` 블록 다음:

```python
        if notice := notices.get(m.id):
            item.ref.lms_notice = notice
```

(`LmsNotice` import 추가.)

`lms.py`:

```python
class LmsSummarySetting(BaseModel):
    min_chars: int = Field(ge=0, le=20000)
```

`LmsStatus`에 `summary_min_chars: int = 300`, `summary_available: bool = False`. `status()`에 `request: Request` 인자를 추가하고 반환에:

```python
        summary_min_chars=await services.lms_summary_min_chars(
            session, config.lms_summary_min_chars
        ),
        summary_available=request.app.state.summarizer is not None,
```

새 엔드포인트(`course_names` 아래):

```python
@router.put("/summary", dependencies=[Depends(local_settings_request)])
async def summary_setting(
    body: LmsSummarySetting, request: Request, session: Session, config: Config
) -> LmsStatus:
    await services.set_lms_summary_min_chars(session, body.min_chars, "user")
    return await status(request, session, config)
```

`lms.py`에서 `await status(session, config)`를 부르는 모든 곳(`course_names`, `prepare_extension` 등 — `grep -n "await status(" backend/src/argos/lms.py`)을 `status(request, session, config)`로 고치고 그 핸들러들에 `request: Request` 인자를 추가.

- [ ] **Step 4: 통과 확인 + 타입 생성**

Run: `cd backend && uv run pytest tests/test_lms.py tests/test_api.py -q && cd .. && make api-types && git diff --stat frontend/src/api-types.ts`
Expected: PASS, `api-types.ts`에 `LmsNoticeOut`, `LmsSummarySetting` 추가

- [ ] **Step 5: Commit**

```bash
git add backend/src/argos/api.py backend/src/argos/lms.py backend/tests/test_lms.py frontend/src/api-types.ts
git commit -m "feat: expose LMS notice cards and the summary setting in the API"
```

---

### Task 6: 프런트 — `LmsNoticeCard`와 피드 연결

**Files:**
- Create: `frontend/src/lmsNotice.tsx`
- Create: `frontend/src/lmsNotice.test.tsx`
- Modify: `frontend/src/api.ts` (`invalidateFor` 123행, 타입 export 22행 부근)
- Modify: `frontend/src/feed.tsx` (`MessageItem` 본문 분기, 약 241행)

**Interfaces:**
- Consumes: `Schemas["LmsNoticeOut"]` (Task 5)
- Produces: `export type LmsNotice = Schemas["LmsNoticeOut"]` (api.ts), `export function LmsNoticeCard({ notice }: { notice: LmsNotice })`, `export function linkify(text: string): React.ReactNode[]`

- [ ] **Step 1: 실패하는 테스트**

`frontend/src/lmsNotice.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import type { LmsNotice } from "./api";
import { LmsNoticeCard } from "./lmsNotice";

const base: LmsNotice = {
  kind: "announcement",
  title: "중간고사 안내",
  text: "범위는 3~7장\n둘째 줄\n셋째 줄\n넷째 줄 https://lms.example/a",
  url: "https://lms.example/1",
  summary: null,
  summary_state: "skipped",
};

test("a summarized notice shows bullets and hides the original until asked", () => {
  render(
    <LmsNoticeCard
      notice={{ ...base, summary: "- 3~7장\n- 101호", summary_state: "done" }}
    />,
  );
  expect(screen.getByText("공지")).toBeTruthy();
  expect(screen.getByText("3~7장")).toBeTruthy();
  expect(screen.queryByText(/둘째 줄/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /원문 보기/ }));
  expect(screen.getByText(/둘째 줄/)).toBeTruthy();
  expect(
    screen.getByRole("link", { name: /LMS에서 열기/ }).getAttribute("href"),
  ).toBe("https://lms.example/1");
});

test("a skipped notice shows the whole original without a toggle", () => {
  render(<LmsNoticeCard notice={base} />);
  expect(screen.getByText(/넷째 줄/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: /원문 보기/ })).toBeNull();
  expect(
    screen.getByRole("link", { name: "https://lms.example/a" }),
  ).toBeTruthy();
});

test("a pending notice shows the first lines and a summarizing hint", () => {
  render(<LmsNoticeCard notice={{ ...base, summary_state: "pending" }} />);
  expect(screen.getByText("요약 중…")).toBeTruthy();
  expect(screen.queryByText(/넷째 줄/)).toBeNull();
});

test("markdown and script links in the original stay plain text", () => {
  render(
    <LmsNoticeCard
      notice={{ ...base, kind: null, text: "[링크](javascript:alert(1)) **굵게**" }}
    />,
  );
  expect(screen.getByText("LMS")).toBeTruthy();
  expect(screen.getByText("[링크](javascript:alert(1)) **굵게**")).toBeTruthy();
  expect(screen.queryAllByRole("link")).toHaveLength(1); // only "LMS에서 열기"
});
```

- [ ] **Step 2: 실패 확인**

Run: `cd frontend && bunx vitest run src/lmsNotice.test.tsx`
Expected: FAIL (모듈 없음)

- [ ] **Step 3: 구현**

`api.ts` 타입 export 근처: `export type LmsNotice = Schemas["LmsNoticeOut"];`
`invalidateFor`의 `keys`에: `lms_notice: feeds, // the card behind an LMS message`

`frontend/src/lmsNotice.tsx`:

```tsx
import { useState } from "react";
import type { LmsNotice } from "./api";
import { tr } from "./i18n";
import { card } from "./ui";

const KINDS: Record<string, () => string> = {
  announcement: () => tr("공지", "Announcement"),
  activity: () => tr("활동", "Activity"),
  conversation: () => tr("메시지", "Message"),
  attendance: () => tr("출석", "Attendance"),
  material: () => tr("자료", "Material"),
};

/** Plain text with http(s) addresses as links: LMS text is never Markdown or HTML. */
export function linkify(text: string): React.ReactNode[] {
  return text.split(/(https?:\/\/[^\s<>"']+)/g).map((part, i) =>
    /^https?:\/\//.test(part) ? (
      // biome-ignore lint/suspicious/noArrayIndexKey: split parts have no identity
      <a key={i} href={part} target="_blank" rel="noreferrer" className="underline">
        {part}
      </a>
    ) : (
      part
    ),
  );
}

export function LmsNoticeCard({ notice }: { notice: LmsNotice }) {
  const [open, setOpen] = useState(false);
  const kind = notice.kind && KINDS[notice.kind] ? KINDS[notice.kind]() : "LMS";
  const summary = notice.summary_state === "done" && notice.summary;
  return (
    <div className={`${card} flex max-w-[640px] flex-col gap-2.5 px-[18px] py-3.5`}>
      <div className="flex items-baseline gap-2">
        <span className="shrink-0 rounded-full bg-inset px-2 py-px text-[11px] font-medium text-text-2">
          {kind}
        </span>
        <span className="min-w-0 font-medium text-ink">{notice.title}</span>
      </div>
      {summary ? (
        <ul className="m-0 flex list-disc flex-col gap-1 pl-5 text-[14px] text-text">
          {summary.split("\n").map((line) => (
            <li key={line}>{line.replace(/^- /, "")}</li>
          ))}
        </ul>
      ) : notice.summary_state === "pending" ? (
        <div className="flex flex-col gap-1">
          <p className="m-0 line-clamp-3 select-text whitespace-pre-wrap text-[14px] text-text-2">
            {notice.text.split("\n").slice(0, 3).join("\n")}
          </p>
          <span className="text-[12px] text-meta">{tr("요약 중…", "Summarizing…")}</span>
        </div>
      ) : (
        <p className="m-0 select-text whitespace-pre-wrap text-[14px] text-text-2">
          {linkify(notice.text)}
        </p>
      )}
      {summary && open && (
        <p className="m-0 select-text whitespace-pre-wrap border-t border-line-soft pt-2.5 text-[13px] text-text-3">
          {linkify(notice.text)}
        </p>
      )}
      <div className="flex items-center gap-4 text-[12px]">
        {summary && (
          <button
            type="button"
            className="text-text-2 hover:text-ink"
            aria-expanded={open}
            onClick={() => setOpen(!open)}
          >
            {open ? "▾ " : "▸ "}
            {tr("원문 보기", "Original")}
          </button>
        )}
        <a
          href={notice.url}
          target="_blank"
          rel="noreferrer"
          className="ml-auto text-text-2 hover:text-ink"
        >
          {tr("LMS에서 열기", "Open in LMS")} ↗
        </a>
      </div>
    </div>
  );
}
```

pending 분기에서 첫 3줄만 보여 주므로 링크화는 하지 않는다(테스트 3과 맞음). skipped 테스트의 링크 이름은 URL 텍스트 그 자체.

`feed.tsx`의 `MessageItem`에서 `{message.run ? (` 바로 앞 분기를 바꾼다:

```tsx
        {message.ref?.lms_notice ? (
          <LmsNoticeCard notice={message.ref.lms_notice} />
        ) : message.run ? (
```

(기존 삼항 체인의 나머지는 그대로, `import { LmsNoticeCard } from "./lmsNotice";` 추가. `const { inbox_item, task, event, approval, debate } = message.ref ?? {};`는 그대로.)

- [ ] **Step 4: 통과 확인**

Run: `cd frontend && bunx vitest run src/lmsNotice.test.tsx && bunx tsc --noEmit && bunx biome check src/lmsNotice.tsx src/lmsNotice.test.tsx src/feed.tsx src/api.ts`
Expected: 4 passed, 오류 없음

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lmsNotice.tsx frontend/src/lmsNotice.test.tsx frontend/src/api.ts frontend/src/feed.tsx
git commit -m "feat: show LMS notices as summary cards in the feed"
```

---

### Task 7: 설정 화면, 디자인 캔버스, 문서

**Files:**
- Modify: `frontend/src/api.ts` (`useLmsCourseNames` 아래)
- Modify: `frontend/src/pages/LmsSettings.tsx` (과목 이름 정리 블록 아래, 약 283행)
- Modify: `frontend/src/pages/LmsSettings.test.tsx`
- Create: `docs/design/lms-notice.dc.html`
- Modify: `docs/superpowers/specs/2026-10-12-lms-summary-design.md`, `docs/decisions.md`, `CLAUDE.md`

**Interfaces:**
- Consumes: `PUT /api/v1/lms/summary`, `LmsStatus.summary_min_chars/summary_available` (Task 5)
- Produces: `useLmsSummarySetting()` mutation `(minChars: number) => LmsStatus`

- [ ] **Step 1: 실패하는 테스트**

`LmsSettings.test.tsx`의 기존 렌더 방식(같은 파일의 "과목 이름 정리" 테스트, 102행 부근)을 따라 상태 응답에 `summary_min_chars: 300, summary_available: true`를 넣고:

```tsx
test("the summary setting turns summaries off and on", async () => {
  // render <LmsSettings /> exactly as the course-name test above does, with
  // status { ...status, summary_min_chars: 300, summary_available: true }
  const toggle = await screen.findByRole("checkbox", { name: /알림 AI 요약/ });
  expect((toggle as HTMLInputElement).checked).toBe(true);
  fireEvent.click(toggle);
  await waitFor(() =>
    expect(requests).toContainEqual({ path: "/api/v1/lms/summary", body: { min_chars: 0 } }),
  );
});
```

(`requests`는 그 파일이 fetch 목을 기록하는 방식에 맞춘다. 기록 장치가 없으면 과목 이름 테스트가 PUT을 확인하는 방법을 그대로 복사한다.)

- [ ] **Step 2: 실패 확인**

Run: `cd frontend && bunx vitest run src/pages/LmsSettings.test.tsx`
Expected: FAIL (체크박스 없음)

- [ ] **Step 3: 구현**

`api.ts`:

```ts
export function useLmsSummarySetting() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (minChars: number) =>
      call(client.PUT("/api/v1/lms/summary", { body: { min_chars: minChars } })),
    onSuccess: (data) => qc.setQueryData(["lms"], data),
  });
}
```

`LmsSettings.tsx`, 과목 목록 `</ul>)}` 다음, 같은 `border-t` 블록 안:

```tsx
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-1 accent-[var(--ink)]"
            checked={(status.data?.summary_min_chars ?? 300) > 0}
            disabled={!status.data || summarySetting.isPending}
            onChange={(event) => summarySetting.mutate(event.target.checked ? 300 : 0)}
          />
          <span>
            {tr("알림 AI 요약", "AI notice summaries")}
            <span className="block text-xs text-meta">
              {status.data?.summary_available
                ? tr(
                    "300자 이상인 LMS 알림을 인박스 분류 모델이 몇 줄로 요약해요. 원문은 카드에서 볼 수 있어요.",
                    "The Inbox classifier model sums up LMS notices of 300+ characters. The original stays on the card.",
                  )
                : tr(
                    "인박스 분류 모델을 설정하면 긴 LMS 알림을 요약해요.",
                    "Set up an Inbox classifier model to summarize long LMS notices.",
                  )}
            </span>
          </span>
        </label>
```

컴포넌트 위쪽 훅 선언부에 `const summarySetting = useLmsSummarySetting();`, `ErrorText`의 error 체인에 `summarySetting.error` 추가.

`docs/design/lms-notice.dc.html`: `docs/design/Main.dc.html`의 구조·토큰을 복사해 피드 한 칸에 LMS 카드 세 상태(요약됨·원문 펼침, 요약 중, 원문만)를 라이트/다크 두 열로 그린다. 색은 `Tokens.dc.html`의 변수만.

- [ ] **Step 4: 문서**

- spec의 "## 설정"을 "`Settings.lms_summary_min_chars`(기본 300, env) 위에 `app_setting.lms_summary_min_chars`. LMS 설정의 '알림 AI 요약' 토글(300/0), `PUT /api/v1/lms/summary`"로, "기존 알림 백필(마이그레이션)"을 "서버 시작 때 `services.backfill_lms_notices`(멱등), `kind`는 None"으로, body 규칙을 `제목\n\n요약\n\nURL`로 고친다.
- `docs/decisions.md`에 한 줄: `| 2026-10-12 | Phase 15a 구현: 요약 기준은 app_setting(과목 이름 정리와 같은 방식), 기존 알림 백필은 마이그레이션 대신 서버 시작 시 멱등 함수, lms_key가 해시라 백필 행의 종류는 비움(배지 "LMS"), 요약 후 body에도 URL 유지 | 앱 코드로 테스트 가능, 마이그레이션이 앱 모듈에 의존하지 않게, 에이전트 컨텍스트에 링크 유지 |`
- `CLAUDE.md` 백엔드 목록의 `attachments.py` 항목 아래에: `- **`summarizer.py`**: LMS 알림 요약(Phase 15a). `import_lms`가 `lms_notice`를 쓰고 요약할 id를 돌려주면 `LmsSummaries` 워커가 분류기의 채팅 모델(`classifier_title_model` 우선)로 하나씩 요약해 `services.save_summary`로 저장. 처리 중 원문이 바뀐 요약은 버리고, 실패는 다음 동기화에 재시도.`

- [ ] **Step 5: 전체 확인**

Run: `make lint && make test && cd backend && uv run alembic check`
Expected: 모두 통과

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api.ts frontend/src/pages/LmsSettings.tsx frontend/src/pages/LmsSettings.test.tsx docs/design/lms-notice.dc.html docs/superpowers/specs/2026-10-12-lms-summary-design.md docs/decisions.md CLAUDE.md
git commit -m "feat: LMS summary toggle in settings, design canvas and docs"
```
