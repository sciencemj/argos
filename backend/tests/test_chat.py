from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
from fakes import FakeClassifier
from fastapi.testclient import TestClient
from sqlalchemy import select

from argos import commands, services
from argos.classifier import ClassifyContext, Suggestion, TitleWriter
from argos.config import Settings
from argos.main import create_app
from argos.models import InboxItem

SEOUL = ZoneInfo("Asia/Seoul")
FRIDAY_DUE = datetime(2026, 9, 25, 23, 59, tzinfo=SEOUL)

HOMEWORK = Suggestion(
    type="task",
    title="과제2 제출",
    due_at=FRIDAY_DUE,
    channel_hint="컴퓨터구조",
    summary="과제2를 금요일까지 제출",
    confidence=0.86,
)


def make_client(settings: Settings, classifier: FakeClassifier | None) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as client:
        client.app.state.classifier = classifier  # type: ignore[attr-defined]
        yield client


@pytest.fixture
def fake() -> FakeClassifier:
    return FakeClassifier(HOMEWORK)


@pytest.fixture
def chat(settings: Settings, fake: FakeClassifier) -> Iterator[TestClient]:
    yield from make_client(settings, fake)


def channel_id(client: TestClient, name: str) -> str:
    channels = client.get("/api/v1/channels").json()["channels"]
    return next(c["id"] for c in channels if c["name"] == name)


def send(client: TestClient, channel: str, body: str, **extra: Any) -> Any:
    response = client.post(f"/api/v1/channels/{channel}/messages", json={"body": body, **extra})
    return response


def feed(client: TestClient, channel: str) -> list[dict[str, Any]]:
    return client.get(f"/api/v1/channels/{channel}/messages").json()["items"]


def test_plain_text_becomes_suggestion_then_task(chat: TestClient, fake: FakeClassifier) -> None:
    """PLAN Phase 3 done-criterion: 금요일까지 과제2 → suggestion card → task on the board."""
    course = channel_id(chat, "컴퓨터구조")
    sent = send(chat, course, "금요일까지 과제2")
    assert sent.status_code == 201
    assert sent.json()["ref_type"] == "inbox_item"

    [message] = feed(chat, course)
    item = message["ref"]["inbox_item"]
    assert item["status"] == "suggested"
    assert item["suggestion_json"]["title"] == "과제2 제출"
    assert item["confidence"] == 0.86

    text, context = fake.calls[0]
    assert text == "금요일까지 과제2"
    assert (context.channel_name, context.channel_kind) == ("컴퓨터구조", "course")

    accepted = chat.post(f"/api/v1/inbox/{item['id']}/accept", json={})
    assert accepted.json()["object_type"] == "task"

    [task] = chat.get("/api/v1/tasks", params={"channel_id": course}).json()
    assert (task["title"], task["status"]) == ("과제2 제출", "todo")
    # "금요일까지" is read from the text against today (models get weekdays wrong).
    friday = commands.find_date("금요일까지", datetime.now(SEOUL).date())
    assert friday is not None
    due = datetime.combine(friday, commands.DEFAULT_DUE, SEOUL).astimezone(UTC)
    assert task["due_at"] == due.strftime("%Y-%m-%dT%H:%M:%SZ")
    [message] = feed(chat, course)
    assert message["ref_type"] == "task"
    assert message["ref"]["task"]["id"] == task["id"]


def test_user_corrections_override_the_suggestion(chat: TestClient) -> None:
    course = channel_id(chat, "컴퓨터구조")
    item_id = send(chat, course, "금요일까지 과제2").json()["ref_id"]
    chat.post(
        f"/api/v1/inbox/{item_id}/accept",
        json={"title": "과제 2", "channel_id": channel_id(chat, "운영체제")},
    )
    [task] = chat.get("/api/v1/tasks", params={"channel_id": channel_id(chat, "운영체제")}).json()
    assert task["title"] == "과제 2"


def test_failed_classification_keeps_raw_text_in_inbox(settings: Settings) -> None:
    for client in make_client(settings, FakeClassifier(error="model timed out")):
        course = channel_id(client, "컴퓨터구조")
        send(client, course, "금요일까지 과제2")
        [item] = client.get("/api/v1/inbox").json()["items"]
        assert item["raw_text"] == "금요일까지 과제2"
        assert item["status"] == "new"
        assert item["suggestion_json"] == {"error": "model timed out"}
        assert client.get("/api/v1/today").json()["inbox_count"] == 1


def test_without_classifier_text_waits_unclassified(settings: Settings) -> None:
    for client in make_client(settings, None):
        course = channel_id(client, "컴퓨터구조")
        send(client, course, "금요일까지 과제2")
        [item] = client.get("/api/v1/inbox").json()["items"]
        assert (item["status"], item["suggestion_json"]) == ("new", None)
        assert client.get("/api/v1/config").json()["classifier_enabled"] is False
        retry = client.post(f"/api/v1/inbox/{item['id']}/classify")
        assert retry.status_code == 422


def test_reclassify_after_failure(settings: Settings) -> None:
    fake = FakeClassifier(error="boom")
    for client in make_client(settings, fake):
        course = channel_id(client, "컴퓨터구조")
        item_id = send(client, course, "금요일까지 과제2").json()["ref_id"]
        fake.error, fake.suggestion = None, HOMEWORK
        retried = client.post(f"/api/v1/inbox/{item_id}/classify")
        assert retried.status_code == 202
        [item] = client.get("/api/v1/inbox").json()["items"]
        assert item["status"] == "suggested"


def test_auto_apply_only_for_enabled_types_above_threshold(settings: Settings) -> None:
    auto = settings.model_copy(
        update={"classifier_auto_apply": ["task"], "classifier_threshold": 0.8}
    )
    for client in make_client(auto, FakeClassifier(HOMEWORK)):
        course = channel_id(client, "컴퓨터구조")
        send(client, course, "금요일까지 과제2")
        [message] = feed(client, course)
        assert message["ref_type"] == "task"
        [item] = client.get("/api/v1/inbox", params={"status": ["accepted"]}).json()["items"]
        assert item["suggestion_json"]["result"]["object_type"] == "task"

    low = settings.model_copy(
        update={"classifier_auto_apply": ["task"], "classifier_threshold": 0.9}
    )
    for client in make_client(low, FakeClassifier(HOMEWORK)):  # same DB, another channel
        course = channel_id(client, "운영체제")
        send(client, course, "금요일까지 과제2")
        [message] = feed(client, course)
        assert message["ref_type"] == "inbox_item"


def test_inbox_capture_without_channel_goes_to_personal(settings: Settings) -> None:
    unsure = HOMEWORK.model_copy(update={"channel_hint": "KUBIG"})  # not a channel
    for client in make_client(settings, FakeClassifier(unsure)):
        inbox = channel_id(client, "inbox")
        item_id = send(client, inbox, "금요일까지 과제2").json()["ref_id"]
        [item] = client.get("/api/v1/inbox").json()["items"]
        assert item["suggestion_json"]["channel_hint"] is None
        client.post(f"/api/v1/inbox/{item_id}/accept", json={})
        [task] = client.get(
            "/api/v1/tasks", params={"channel_id": channel_id(client, "일상")}
        ).json()
        assert task["title"] == "과제2 제출"

        other = send(client, inbox, "금요일까지 과제3").json()["ref_id"]
        chosen = client.post(
            f"/api/v1/inbox/{other}/accept", json={"channel_id": channel_id(client, "운영체제")}
        )
        assert chosen.status_code == 200


def test_slash_commands(chat: TestClient, fake: FakeClassifier) -> None:
    course = channel_id(chat, "컴퓨터구조")
    task = send(chat, course, "/task 실습3 2026-09-28").json()
    assert task["ref"]["task"]["due_at"] == "2026-09-28T14:59:00Z"
    event = send(chat, course, "/event 퀴즈 2026-09-30 10:30-11:15").json()
    assert event["ref"]["event"]["starts_at"] == "2026-09-30T01:30:00Z"
    note = send(chat, course, "/note LRU vs Clock").json()
    assert note["ref"]["inbox_item"]["suggestion_json"]["type"] == "study_note"
    assert chat.post(f"/api/v1/inbox/{note['ref_id']}/accept", json={}).status_code == 422
    assert fake.calls == []  # commands never reach the classifier

    ask = send(chat, course, "/ask forwarding이 뭐야").json()
    assert ask["ref_type"] is None  # a question goes to the default agent, not the inbox
    thread = chat.get(f"/api/v1/messages/{ask['id']}/thread").json()
    assert [r["author_type"] for r in thread["replies"]] == ["agent"]


def test_bad_command_is_rejected_and_not_stored(chat: TestClient) -> None:
    course = channel_id(chat, "컴퓨터구조")
    bad = send(chat, course, "/event 회의")
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "invalid"
    assert feed(chat, course) == []


def test_threads_pins_and_quick_convert(chat: TestClient) -> None:
    course = channel_id(chat, "컴퓨터구조")
    root = send(chat, course, "금요일까지 과제2").json()
    reply = send(chat, course, "조교한테 물어보기", thread_root_id=root["id"]).json()
    nested = send(chat, course, "대댓글", thread_root_id=reply["id"]).json()
    assert nested["thread_root_id"] == root["id"]  # one level deep

    [top] = feed(chat, course)
    assert top["reply_count"] == 2
    assert chat.get("/api/v1/inbox").json()["items"][0]["raw_text"] == "금요일까지 과제2"

    pinned = chat.patch(f"/api/v1/messages/{root['id']}", json={"pinned": True}).json()
    assert pinned["pinned"] is True

    converted = chat.post(f"/api/v1/messages/{root['id']}/convert", json={"kind": "task"})
    assert converted.json()["object_type"] == "task"
    again = chat.post(f"/api/v1/messages/{root['id']}/convert", json={"kind": "task"})
    assert again.status_code == 409


def test_message_created_event_over_websocket(chat: TestClient) -> None:
    course = channel_id(chat, "컴퓨터구조")
    with chat.websocket_connect("/ws") as ws:
        send(chat, course, "/task 알림 확인")
        events = [ws.receive_json() for _ in range(2)]
    types = {e["type"] for e in events}
    assert {"object.created", "message.created"} == types


class RacingClassifier(FakeClassifier):
    """While the "model" thinks, the user accepts the item from another request."""

    def __init__(self, sessionmaker: Any) -> None:
        super().__init__(HOMEWORK)
        self.sessionmaker = sessionmaker

    async def classify(self, text: str, context: ClassifyContext) -> Suggestion:
        async with self.sessionmaker() as session:
            item = await session.scalar(select(InboxItem).where(InboxItem.raw_text == text))
            await services.accept_inbox_item(
                session, item.id, actor="user", overrides={"type": "task", "title": "직접 정리"}
            )
        return await super().classify(text, context)


def test_late_classification_does_not_reopen_a_handled_item(settings: Settings) -> None:
    for client in make_client(settings, None):
        client.app.state.classifier = RacingClassifier(client.app.state.sessionmaker)  # type: ignore[attr-defined]
        course = channel_id(client, "컴퓨터구조")
        send(client, course, "캐시 매핑 방식 정리하기")

        [item] = client.get("/api/v1/inbox", params={"status": ["accepted"]}).json()["items"]
        assert item["suggestion_json"]["result"]["object_type"] == "task"
        [message] = feed(client, course)
        assert message["ref"]["task"]["title"] == "직접 정리"


class FakeTitles(TitleWriter):
    def __init__(self, title: str | None) -> None:
        super().__init__(cast(Any, None), "chat", sequential=True)
        self.reply = title
        self.asked: list[str] = []

    async def title(self, text: str, language: str) -> str | None:
        self.asked.append(text)
        return self.reply


class SequentialClassifier(FakeClassifier):
    """A decision classifier in sequential mode: its title comes after the suggestion."""

    def __init__(self, suggestion: Suggestion, titles: FakeTitles) -> None:
        super().__init__(suggestion)
        self.later_titles = titles


def test_sequential_title_lands_before_auto_apply(settings: Settings) -> None:
    auto = settings.model_copy(
        update={"classifier_auto_apply": ["task"], "classifier_threshold": 0.8}
    )
    titles = FakeTitles("과제2 제출하기")
    for client in make_client(auto, SequentialClassifier(HOMEWORK, titles)):
        course = channel_id(client, "컴퓨터구조")
        send(client, course, "금요일까지 과제2")
        assert titles.asked == ["금요일까지 과제2"]
        [task] = client.get("/api/v1/tasks", params={"channel_id": course}).json()
        assert task["title"] == "과제2 제출하기"

    idea = HOMEWORK.model_copy(update={"type": "idea", "due_at": None})
    quiet = FakeTitles("쓰이면 안 됨")
    for client in make_client(settings, SequentialClassifier(idea, quiet)):
        course = channel_id(client, "운영체제")
        send(client, course, "과제2 미리 해볼까")
        [message] = feed(client, course)
        assert message["ref"]["inbox_item"]["suggestion_json"]["title"] == "과제2 제출"
        assert quiet.asked == []  # only tasks and events get a written title

    failed = FakeTitles(None)
    for client in make_client(settings, SequentialClassifier(HOMEWORK, failed)):
        course = channel_id(client, "운영체제")
        send(client, course, "금요일까지 과제3")
        item = feed(client, course)[-1]["ref"]["inbox_item"]
        assert item["suggestion_json"]["title"] == "과제2 제출"  # kept the first title
