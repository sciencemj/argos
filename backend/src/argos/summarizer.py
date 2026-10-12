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
