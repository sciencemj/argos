import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx2 as httpx
import pytest
from openai import AsyncOpenAI

from argos.classifier import (
    ClassifierError,
    ClassifyContext,
    OpenAICompatClassifier,
    Suggestion,
    _parse_json,  # pyright: ignore[reportPrivateUsage]
    anchor_dates,
    build_classifier,
    build_prompt,
)
from argos.config import Settings

SEOUL = ZoneInfo("Asia/Seoul")
CONTEXT = ClassifyContext(
    now=datetime(2026, 9, 24, 12, tzinfo=SEOUL),
    tz=SEOUL,
    channel_name="컴퓨터구조",
    channel_kind="course",
    channel_names=["컴퓨터구조", "운영체제"],
)
ANSWER: dict[str, Any] = {
    "type": "task",
    "title": "과제2 제출",
    "due_at": "2026-09-25T23:59:00",  # no offset: must be read as Seoul time
    "starts_at": None,
    "ends_at": None,
    "all_day_date": None,
    "channel_hint": "#컴퓨터구조",
    "tags": [],
    "summary": "과제2를 금요일까지 제출",
    "confidence": 0.9,
}


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


def classifier(handler: Any) -> OpenAICompatClassifier:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AsyncOpenAI(
        base_url="http://llm.test/v1", api_key="k", http_client=http, max_retries=0
    )
    return OpenAICompatClassifier(client, "m", reasoning_effort="none")


async def test_structured_output_request_and_parse() -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=completion(json.dumps(ANSWER, ensure_ascii=False)))

    c = classifier(handler)
    result = await c.classify("금요일까지 과제2", CONTEXT)
    assert result.due_at == datetime(2026, 9, 25, 23, 59, tzinfo=SEOUL)
    assert result.channel_hint == "컴퓨터구조"
    assert requests[0]["response_format"]["type"] == "json_schema"
    assert requests[0]["reasoning_effort"] == "none"

    await c.classify("금요일까지 과제2", CONTEXT)
    assert len(requests) == 1  # cached


async def test_falls_back_to_prompt_json_when_schema_rejected() -> None:
    seen: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append("response_format" in body)
        if "response_format" in body:
            return httpx.Response(400, json={"error": {"message": "response_format unsupported"}})
        fenced = "<think>hmm</think>\n```json\n" + json.dumps(ANSWER, ensure_ascii=False) + "\n```"
        return httpx.Response(200, json=completion(fenced))

    result = await classifier(handler).classify("금요일까지 과제2", CONTEXT)
    assert result.title == "과제2 제출"
    assert seen == [True, False]


@pytest.mark.parametrize(
    ("status", "content"),
    [(500, "server down"), (200, "그냥 문장"), (200, json.dumps(ANSWER | {"confidence": 3}))],
)
async def test_failures_become_classifier_errors(status: int, content: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, text=content)
        return httpx.Response(200, json=completion(content))

    with pytest.raises(ClassifierError):
        await classifier(handler).classify("금요일까지 과제2", CONTEXT)


def test_event_suggestion_needs_a_time() -> None:
    with pytest.raises(ValueError):
        Suggestion.model_validate(ANSWER | {"type": "event"})


def test_parse_json_variants() -> None:
    assert _parse_json('앞말 {"a": 1} 뒷말') == {"a": 1}
    assert _parse_json('<think>{"x": 0}</think>{"a": 2}') == {"a": 2}


def test_prompt_carries_channel_context_and_date() -> None:
    system = build_prompt("x", CONTEXT)[0]["content"]
    assert "2026-09-24 (목)" in system
    assert '"#컴퓨터구조" 채널' in system


def test_build_classifier_is_off_without_model() -> None:
    assert build_classifier(Settings(classifier_model="")) is None
    assert build_classifier(Settings(classifier_model="qwen")) is not None


def test_anchor_dates_corrects_the_models_day() -> None:
    wrong_task = Suggestion.model_validate(
        ANSWER | {"due_at": "2026-10-07T23:59:00+09:00", "title": "KUBIG 발표자료"}
    )
    fixed = anchor_dates(wrong_task, "KUBIG 발표자료 다음주 수요일까지", CONTEXT)
    assert fixed.due_at == datetime(2026, 9, 30, 23, 59, tzinfo=SEOUL)

    wrong_event = Suggestion.model_validate(
        ANSWER
        | {
            "type": "event",
            "due_at": None,
            "starts_at": "2026-10-05T15:00:00+09:00",
            "ends_at": "2026-10-05T16:00:00+09:00",
        }
    )
    fixed = anchor_dates(wrong_event, "다음주 화요일 오후 3시 조교 면담", CONTEXT)
    assert fixed.starts_at == datetime(2026, 9, 29, 15, tzinfo=SEOUL)
    assert fixed.ends_at == datetime(2026, 9, 29, 16, tzinfo=SEOUL)

    idea = Suggestion.model_validate(ANSWER | {"type": "idea", "due_at": None})
    assert anchor_dates(idea, "금요일에 해보기", CONTEXT) == idea
