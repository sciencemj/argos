"""AI summaries of LMS notices (PLAN Phase 15a), written with the Inbox classifier's
chat model. A notice without a summary still shows its original text."""

import asyncio
import logging
import re
from collections.abc import Callable, Iterable
from typing import Protocol

from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from argos import services
from argos.classifier import DEFAULT_BASE_URLS
from argos.config import Settings
from argos.models import LmsNotice

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
    def __init__(
        self, client: AsyncOpenAI, model: str, *, reasoning_effort: str | None = None
    ) -> None:
        self._client = client
        self.model = model
        # Only sent when asked for: some backends reject the field.
        self.extra: dict[str, str] = (
            {"reasoning_effort": reasoning_effort} if reasoning_effort else {}
        )

    async def summarize(self, title: str, text: str, language: str) -> str:
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": PROMPT.get(language, PROMPT["ko"])},
                    {"role": "user", "content": f"{title}\n\n{text[:MAX_SOURCE_CHARS]}"},
                ],
                temperature=0,
                extra_body=self.extra,
            )
            content = response.choices[0].message.content or ""
        except Exception as exc:
            raise SummaryError(f"summary request failed: {type(exc).__name__}") from exc
        return parse_bullets(content)


def classifier_chat(settings: Settings) -> tuple[AsyncOpenAI, str, str | None] | None:
    """Client, model and reasoning effort of the classifier's chat model: the title
    writer when the classifier is a decision-only model. None when none is set up."""
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
    effort = settings.classifier_reasoning_effort  # the classifier's rule (build_classifier)
    if effort is None and settings.classifier_provider == "ollama":
        effort = "none"
    return client, model, effort


def build_summarizer(settings: Settings) -> Summarizer | None:
    chat = classifier_chat(settings)
    if chat is None:
        return None
    client, model, effort = chat
    return OpenAISummarizer(client, model, reasoning_effort=effort)


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

    async def _enabled(self, session: AsyncSession) -> bool:
        default = self._settings().lms_summary_min_chars
        return await services.lms_summary_min_chars(session, default) > 0

    async def process(self, notice_id: str) -> None:
        summarizer = self._summarizer()
        async with self._sessionmaker() as session:
            notice = await session.get(LmsNotice, notice_id)
            if notice is None or notice.summary_state != "pending":
                return
            title, text = notice.title, notice.text
            if not await self._enabled(session):  # turned off after it was queued
                await services.mark_summary(session, notice_id, text, "skipped")
                return
            if summarizer is None:  # the classifier was turned off meanwhile
                await services.fail_summary(session, notice_id, text)
                return
            try:
                summary = await summarizer.summarize(title, text, self._settings().language)
                await services.save_summary(session, notice_id, text, summary)
            except Exception as exc:
                # Anything (the model, a locked database) leaves it failed, so the next
                # sync tries again instead of the card waiting until a restart.
                if isinstance(exc, SummaryError):
                    log.warning("LMS summary failed: %s", exc)
                else:
                    log.exception("LMS summary failed")
                await session.rollback()
                try:
                    await services.fail_summary(session, notice_id, text)
                except Exception:
                    log.exception("could not mark the LMS summary failed")

    async def start(self) -> None:
        """Cards for older LMS messages, then the notices left waiting at shutdown."""
        async with self._sessionmaker() as session:
            await services.backfill_lms_notices(session)
            if await self._enabled(session):
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
