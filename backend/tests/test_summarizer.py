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
