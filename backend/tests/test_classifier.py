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
    DecisionClassifier,
    OpenAICompatClassifier,
    Suggestion,
    TitleWriter,
    _parse_json,  # pyright: ignore[reportPrivateUsage]
    anchor_dates,
    build_classifier,
    build_prompt,
    decision_request,
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


def test_english_classifier_prompt() -> None:
    english = ClassifyContext(
        now=CONTEXT.now,
        tz=CONTEXT.tz,
        channel_name="Computer Architecture",
        channel_kind="course",
        channel_names=["Computer Architecture"],
        language="en",
    )
    system = build_prompt("Submit assignment tomorrow", english)[0]["content"]
    assert "Reply with exactly one JSON object" in system
    assert "tomorrow" in system
    assert 'channel_hint to "Computer Architecture"' in system


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


def test_prompt_suggests_personal_channel_from_inbox() -> None:
    from_inbox = ClassifyContext(
        now=CONTEXT.now,
        tz=SEOUL,
        channel_name="inbox",
        channel_kind="system",
        channel_names=["컴퓨터구조", "일상"],
        personal_channel="일상",
    )
    assert 'channel_hint는 "일상"' in build_prompt("치과 예약", from_inbox)[0]["content"]


FROM_INBOX = ClassifyContext(
    now=CONTEXT.now,
    tz=SEOUL,
    channel_name="inbox",
    channel_kind="system",
    channel_names=["컴퓨터구조", "일상"],
    personal_channel="일상",
)


def choice(picked: str, probabilities: dict[str, float]) -> dict[str, Any]:
    return {"type": "choice", "choice": picked, "probabilities": probabilities, "confidence": 0.9}


def ollama(handler: Any, model: str = "nimble") -> Any:
    """build_classifier with Ollama's HTTP calls answered by `handler`."""
    real = httpx.AsyncClient

    def client(**kw: Any) -> httpx.AsyncClient:
        return real(transport=httpx.MockTransport(handler), **kw)

    import argos.classifier as module

    original = module.httpx2.AsyncClient
    module.httpx2.AsyncClient = client  # pyright: ignore[reportAttributeAccessIssue]
    try:
        return build_classifier(Settings(classifier_model=model))
    finally:
        module.httpx2.AsyncClient = original  # pyright: ignore[reportAttributeAccessIssue]


async def test_decision_model_picks_type_and_channel() -> None:
    sent: list[dict[str, Any]] = []
    shown: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            shown.append(json.loads(request.content)["model"])
            return httpx.Response(200, json={"capabilities": ["decision"]})
        assert request.url.path == "/v1/systemone"
        sent.append(json.loads(request.content))
        answers = {
            "type": choice("task", {"task": 0.9, "event": 0.05, "idea": 0.03, "study_note": 0.02}),
            "channel": choice("컴퓨터구조", {"컴퓨터구조": 0.8, "일상": 0.1, "-": 0.1}),
        }
        return httpx.Response(200, json={"model": "nimble", "answers": answers, "usage": {}})

    classifier = ollama(handler)
    assert classifier is not None
    got = await classifier.classify("과제2 제출 다음주 수요일까지", FROM_INBOX)
    await classifier.classify("다른 메시지", FROM_INBOX)
    assert shown == ["nimble"]  # asked once what the model is
    assert sent[0]["state"] == "과제2 제출 다음주 수요일까지"
    assert set(sent[0]["questions"]["channel"]["criteria"]) == {"컴퓨터구조", "일상", "-"}
    assert got.type == "task"
    assert got.title == "과제2 제출"
    assert got.channel_hint == "컴퓨터구조"
    assert got.due_at == datetime(2026, 9, 30, 23, 59, tzinfo=SEOUL)
    assert got.confidence == pytest.approx(0.72)


async def test_decision_event_without_a_date_falls_back() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["decision"]})
        answers = {
            "type": choice("event", {"task": 0.1, "event": 0.6, "idea": 0.25, "study_note": 0.05})
        }
        return httpx.Response(200, json={"model": "nimble", "answers": answers, "usage": {}})

    classifier = ollama(handler)
    assert classifier is not None
    got = await classifier.classify("조교 면담 잡기", CONTEXT)
    assert (got.type, got.channel_hint, got.confidence) == ("idea", "컴퓨터구조", 0.25)

    timed = await classifier.classify("내일 오후 3시 조교 면담", CONTEXT)
    assert timed.type == "event"
    assert timed.starts_at == datetime(2026, 9, 25, 15, tzinfo=SEOUL)
    assert timed.title == "조교 면담"


async def test_chat_model_still_uses_chat_endpoint() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={"capabilities": ["completion"]})

    classifier = ollama(handler, model="qwen")
    assert classifier is not None
    picked = await classifier._pick()  # pyright: ignore[reportAttributeAccessIssue]
    assert isinstance(picked, OpenAICompatClassifier)
    assert paths == ["/api/show"]


def test_decision_request_skips_channel_when_fixed() -> None:
    body = decision_request("tev1", "x", CONTEXT)
    assert list(body["questions"]) == ["type"]


async def test_decision_trusts_a_channel_named_in_the_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["decision"]})
        answers = {
            "type": choice("task", {"task": 0.9, "event": 0.1}),
            "channel": choice("일상", {"컴퓨터구조": 0.2, "일상": 0.7, "-": 0.1}),
        }
        return httpx.Response(200, json={"model": "nimble", "answers": answers, "usage": {}})

    classifier = ollama(handler)
    assert classifier is not None
    got = await classifier.classify("컴퓨터구조 실습 보고서", FROM_INBOX)
    assert (got.channel_hint, got.confidence) == ("컴퓨터구조", 0.9)


async def test_missing_model_is_named_in_the_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    classifier = ollama(handler)
    assert classifier is not None
    with pytest.raises(ClassifierError, match="not installed"):
        await classifier.classify("x", FROM_INBOX)


def decision_with_titles(kind: str, title_reply: int | str) -> DecisionClassifier:
    """A decision classifier answering `kind`, with a title model that replies
    `title_reply` (text, or an HTTP status for a failure)."""

    def systemone(request: httpx.Request) -> httpx.Response:
        answers = {"type": choice(kind, {kind: 0.9, "task": 0.05, "idea": 0.05})}
        return httpx.Response(200, json={"model": "tev1", "answers": answers, "usage": {}})

    def chat(request: httpx.Request) -> httpx.Response:
        if isinstance(title_reply, int):
            return httpx.Response(title_reply, json={"error": "boom"})
        sent = json.loads(request.content)
        assert sent["model"] == "gemma"
        assert sent["messages"][1]["content"] == "운영체제 과제3 다음주 수요일까지 제출"
        return httpx.Response(200, json=completion(title_reply))

    openai = AsyncOpenAI(
        base_url="http://llm.test/v1",
        api_key="k",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(chat)),
        max_retries=0,
    )
    return DecisionClassifier(
        httpx.AsyncClient(transport=httpx.MockTransport(systemone)),
        "http://ollama.test",
        "tev1",
        TitleWriter(openai, "gemma"),
    )


async def test_title_model_names_tasks_for_a_decision_model() -> None:
    text = "운영체제 과제3 다음주 수요일까지 제출"
    got = await decision_with_titles("task", '제목: "운영체제 과제3 제출"\n').classify(
        text, CONTEXT
    )
    assert got.title == "운영체제 과제3 제출"
    assert got.due_at == datetime(2026, 9, 30, 23, 59, tzinfo=SEOUL)

    failed = await decision_with_titles("task", 500).classify(text, CONTEXT)
    assert failed.title == "운영체제 과제3 제출"  # from the text: dates dropped

    note = await decision_with_titles("study_note", "쓰이면 안 됨").classify(text, CONTEXT)
    assert note.title == "운영체제 과제3 제출"  # only tasks and events get a written title


async def test_sequential_mode_leaves_the_title_for_later() -> None:
    classifier = decision_with_titles("task", 500)
    classifier._titles.sequential = True  # pyright: ignore[reportPrivateUsage, reportOptionalMemberAccess]
    got = await classifier.classify("운영체제 과제3 다음주 수요일까지 제출", CONTEXT)
    assert got.title == "운영체제 과제3 제출"
    assert classifier.later_titles is not None
