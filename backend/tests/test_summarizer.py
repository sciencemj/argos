import json
from typing import Any

import httpx2 as httpx
import pytest
from fakes import FakeSummarizer
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from argos import services
from argos.config import Settings
from argos.db import make_engine, make_sessionmaker
from argos.models import LmsNotice
from argos.summarizer import (
    MAX_SOURCE_CHARS,
    LmsSummaries,
    OpenAISummarizer,
    SummaryError,
    build_summarizer,
    parse_bullets,
)

LONG = "기말 프로젝트 발표 순서와 준비물을 안내합니다. " * 20
Maker = async_sessionmaker[AsyncSession]


def completion(content: str) -> dict[str, Any]:
    return {
        "id": "x",
        "object": "chat.completion",
        "created": 0,
        "model": "m",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
    }


def summarizer(handler: Any) -> OpenAISummarizer:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AsyncOpenAI(
        base_url="http://llm.test/v1", api_key="k", http_client=http, max_retries=0
    )
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
    chat = settings.model_copy(
        update={"classifier_model": "decide", "classifier_title_model": "writer"}
    )
    built = build_summarizer(chat)
    assert isinstance(built, OpenAISummarizer) and built.model == "writer"
    plain = settings.model_copy(update={"classifier_model": "qwen"})
    built = build_summarizer(plain)
    assert isinstance(built, OpenAISummarizer) and built.model == "qwen"


async def seeded(settings: Settings) -> tuple[Maker, str]:
    maker = make_sessionmaker(make_engine(settings.db_url))
    async with maker() as session:
        result = await services.import_lms(
            session,
            account_id="42",
            courses=[{"id": "7", "name": "알고리즘"}],
            items=[
                {
                    "id": "1",
                    "course_id": "7",
                    "kind": "announcement",
                    "title": "발표",
                    "text": LONG,
                    "url": "https://lms.example/1",
                    "due_at": None,
                    "file": None,
                }
            ],
            summary_min_chars=300,
        )
    return maker, result["summarize"][0]


async def state(maker: Maker, notice_id: str) -> LmsNotice:
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
    fake = FakeSummarizer(error=True)
    await LmsSummaries(maker, lambda: fake, lambda: settings).process(notice_id)
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

    fake = ChangesWhileSummarizing()
    await LmsSummaries(maker, lambda: fake, lambda: settings).process(notice_id)
    row = await state(maker, notice_id)
    assert (row.summary_state, row.summary) == ("pending", None)


async def test_start_requeues_pending_and_processes(settings: Settings) -> None:
    maker, notice_id = await seeded(settings)
    fake = FakeSummarizer("- 완료")
    worker = LmsSummaries(maker, lambda: fake, lambda: settings)
    await worker.start()
    await worker.join()
    await worker.stop()
    assert (await state(maker, notice_id)).summary_state == "done"
