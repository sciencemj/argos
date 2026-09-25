"""Debates (PLAN Phase 10) with fake agents: turn order, the shared record, user
interjections, limits, cancel, and the summary card's actions."""

import asyncio
import threading
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from fakes import FakeAgent, fake_agents
from fastapi.testclient import TestClient

from argos import commands
from argos.agents import AgentEvent, Token, Turn
from argos.config import Settings
from argos.main import create_app


class Scripted(FakeAgent):
    """Answers from a script, one entry per call; waits at `gate` (a threading.Event,
    safe across the test thread and the app's loop) when given."""

    def __init__(self, name: str, answers: list[str], gate: threading.Event | None = None) -> None:
        super().__init__(name, [])
        self.answers = answers
        self.gate = gate
        self.calls = 0

    async def stream(
        self, transcript: list[Turn], context: str, session: str
    ) -> AsyncIterator[AgentEvent]:
        self.transcripts.append(transcript)
        self.contexts.append(context)
        answer = self.answers[min(self.calls, len(self.answers) - 1)]
        self.calls += 1
        while self.gate is not None and not self.gate.is_set():  # noqa: ASYNC110
            await asyncio.sleep(0.01)  # a threading.Event: set from the test's thread
        yield Token(answer)


def make(settings: Settings, **agents: FakeAgent) -> TestClient:
    client = TestClient(create_app(settings))
    client.__enter__()
    client.app.state.runner.adapter_factory = fake_agents(**agents)  # type: ignore[attr-defined]
    return client


@pytest.fixture
def cast() -> dict[str, Scripted]:
    return {
        "claude": Scripted("claude", ["클로드 1라운드", "클로드 2라운드"]),
        "codex": Scripted("codex", ["코덱스 1라운드", "코덱스 2라운드"]),
        "local": Scripted("local", ["로컬 1라운드", "로컬 2라운드"]),
        "hermes": Scripted("hermes", ["## 요약\n세 입장이 갈렸다.\n## 결론\n캐시부터 공부하자."]),
    }


@pytest.fixture
def app(settings: Settings, cast: dict[str, Scripted]) -> Iterator[TestClient]:
    client = make(settings, **cast)
    yield client
    client.__exit__(None, None, None)


def course(client: TestClient) -> str:
    return next(
        c["id"]
        for c in client.get("/api/v1/channels").json()["channels"]
        if c["name"] == "컴퓨터구조"
    )


def start(client: TestClient, body: str) -> dict[str, Any]:
    sent = client.post(f"/api/v1/channels/{course(client)}/messages", json={"body": body})
    assert sent.status_code == 201, sent.text
    return sent.json()


def wait(client: TestClient, debate_id: str, *statuses: str, seconds: float = 5) -> dict[str, Any]:
    deadline = time.time() + seconds
    while time.time() < deadline:
        debate = client.get(f"/api/v1/debates/{debate_id}").json()
        if debate["status"] in statuses:
            return debate
        time.sleep(0.02)
    raise AssertionError(f"debate did not reach {statuses}")


def thread(client: TestClient, root_id: str) -> list[dict[str, Any]]:
    return client.get(f"/api/v1/messages/{root_id}/thread").json()["replies"]


def test_parse_debate_commands() -> None:
    now = commands.datetime.now(commands.ZoneInfo("Asia/Seoul"))
    tz = commands.ZoneInfo("Asia/Seoul")
    parsed = commands.parse("/debate @claude @codex 캐시 정책 --rounds 2 --tools", now, tz)
    assert parsed == commands.DebateCommand(
        ("claude", "codex"), "캐시 정책", "round_robin", 2, True
    )
    for bad, text in [
        ("/debate @claude 혼자", "두~네"),
        ("/debate @a @b @c 주제 --mode pro_con", "두 에이전트"),
        ("/debate @a @b 주제 --rounds 9", "1~6"),
        ("/debate @a @a 주제", "두 번"),
        ("/debate @a @b 주제 --mode free", "--mode"),
    ]:
        with pytest.raises(commands.CommandError, match=text):
            commands.parse(bad, now, tz)


def test_three_way_debate_ends_within_its_rounds_with_a_summary(
    app: TestClient, cast: dict[str, Scripted]
) -> None:
    """PLAN Phase 10 done-criterion: a 3-agent debate ends within the round limit and
    leaves a summary card."""
    root = start(app, "/debate @claude @codex @local L1 캐시는 필요한가 --rounds 2")
    debate_id = root["ref"]["debate"]["id"]
    debate = wait(app, debate_id, "done")
    assert (debate["rounds_done"], debate["max_rounds"]) == (2, 2)
    assert debate["moderator"] == "hermes"  # the app's default agent
    assert debate["summary"].startswith("## 요약")

    replies = thread(app, root["id"])
    speakers = [r["author_id"] for r in replies]
    assert speakers == ["claude", "codex", "local"] * 2 + ["hermes"]  # turns, then summary
    assert all(r["run"]["kind"] == "debate" for r in replies)

    # Each speaker sees the others' words, marked with who said them.
    codex_first = cast["codex"].transcripts[0][0].text
    assert "[claude]: 클로드 1라운드" in codex_first
    assert "[local]:" not in codex_first  # local had not spoken yet
    assert "L1 캐시는 필요한가" in cast["codex"].contexts[0]
    assert cast["claude"].no_tools == [True, True]  # no Argos tools unless --tools

    [card] = [
        m
        for m in app.get(f"/api/v1/channels/{course(app)}/messages").json()["items"]
        if m["id"] == root["id"]
    ]
    assert card["ref"]["debate"]["status"] == "done"
    assert card["agent_replies"] == []  # turns stay in the thread, not under the card


def test_user_can_step_in_and_the_next_speaker_hears_it(settings: Settings) -> None:
    gate = threading.Event()
    claude = Scripted("claude", ["첫 발언"], gate)
    codex = Scripted("codex", ["둘째 발언"])
    hermes = Scripted("hermes", ["## 요약\n끝"])
    app = make(settings, claude=claude, codex=codex, hermes=hermes)
    try:
        root = start(app, "/debate @claude @codex 테스트 우선 개발 --rounds 1")
        debate_id = root["ref"]["debate"]["id"]
        time.sleep(0.2)
        cut_in = app.post(
            f"/api/v1/channels/{course(app)}/messages",
            json={"body": "보안 관점도 다뤄 줘", "thread_root_id": root["id"]},
        )
        assert cut_in.status_code == 201
        gate.set()
        wait(app, debate_id, "done")
        assert "[사용자]: 보안 관점도 다뤄 줘" in codex.transcripts[0][0].text
        authors = [r["author_id"] or r["author_type"] for r in thread(app, root["id"])]
        assert authors.count("codex") == 1 and authors.count("claude") == 1  # no extra runs
    finally:
        app.__exit__(None, None, None)


def test_moderator_picks_speakers_and_can_end_early(settings: Settings) -> None:
    claude = Scripted("claude", ["클로드"])
    codex = Scripted("codex", ["코덱스"])
    hermes = Scripted("hermes", ["NEXT: codex", "END", "## 요약\n짧게 끝"])
    app = make(settings, claude=claude, codex=codex, hermes=hermes)
    try:
        root = start(app, "/debate @claude @codex 주제 --mode moderated --rounds 3")
        debate = wait(app, root["ref"]["debate"]["id"], "done")
        speakers = [r["author_id"] for r in thread(app, root["id"]) if r["author_type"] == "agent"]
        assert speakers == ["codex", "hermes"]  # codex, then the moderator's summary
        assert debate["rounds_done"] == 0
        assert any(
            "사회자" in r["body"] for r in thread(app, root["id"]) if r["author_type"] == "system"
        )
    finally:
        app.__exit__(None, None, None)


def test_pro_con_assigns_sides(app: TestClient, cast: dict[str, Scripted]) -> None:
    root = start(app, "/debate @claude @codex 원격 수업 --mode pro_con --rounds 1")
    wait(app, root["ref"]["debate"]["id"], "done")
    assert "너의 입장: 찬성" in cast["claude"].contexts[0]
    assert "너의 입장: 반대" in cast["codex"].contexts[0]


def test_cancel_stops_without_a_summary(settings: Settings) -> None:
    gate = threading.Event()  # never opened: the first turn hangs
    app = make(settings, claude=Scripted("claude", ["…"], gate), codex=Scripted("codex", ["…"]))
    try:
        root = start(app, "/debate @claude @codex 주제")
        debate_id = root["ref"]["debate"]["id"]
        time.sleep(0.2)
        app.post(f"/api/v1/debates/{debate_id}/cancel")
        debate = wait(app, debate_id, "cancelled")
        assert debate["summary"] is None
        assert [r["author_id"] for r in thread(app, root["id"])] == ["claude"]
    finally:
        gate.set()
        app.__exit__(None, None, None)


def test_time_budget_ends_early_but_still_summarizes(
    settings: Settings, cast: dict[str, Scripted]
) -> None:
    app = make(settings.model_copy(update={"debate_budget_seconds": 0}), **cast)
    try:
        root = start(app, "/debate @claude @codex 주제")
        debate = wait(app, root["ref"]["debate"]["id"], "done")
        assert debate["summary"] is not None
        notes = [r["body"] for r in thread(app, root["id"]) if r["author_type"] == "system"]
        assert any("시간 예산" in n for n in notes)
    finally:
        app.__exit__(None, None, None)


def test_summary_becomes_a_task_once(app: TestClient) -> None:
    root = start(app, "/debate @claude @codex 캐시 --rounds 1")
    debate_id = root["ref"]["debate"]["id"]
    wait(app, debate_id, "done")
    first = app.post(f"/api/v1/debates/{debate_id}/task").json()
    again = app.post(f"/api/v1/debates/{debate_id}/task").json()
    assert first["summary_task_id"] == again["summary_task_id"] is not None
    task = app.get(f"/api/v1/tasks/{first['summary_task_id']}").json()
    assert task["title"] == "토론 결론: 캐시" and "캐시부터 공부하자" in task["description"]
    note = app.post(f"/api/v1/debates/{debate_id}/note")
    assert note.status_code == 422  # no vault connected in tests


def test_unknown_participant_is_refused(app: TestClient) -> None:
    refused = app.post(
        f"/api/v1/channels/{course(app)}/messages", json={"body": "/debate @claude @nobody 주제"}
    )
    assert refused.status_code == 422
    assert "@nobody" in refused.json()["error"]["message"]


# --- commands inside a thread ----------------------------------------------------------


def reply(client: TestClient, root_id: str, body: str) -> Any:
    return client.post(
        f"/api/v1/channels/{course(client)}/messages",
        json={"body": body, "thread_root_id": root_id},
    )


def test_slash_commands_work_in_threads(app: TestClient, cast: dict[str, Scripted]) -> None:
    root = app.post(f"/api/v1/channels/{course(app)}/messages", json={"body": "과제 계획 이야기"})
    root_id = root.json()["id"]
    task = reply(app, root_id, "/task 보고서 초안 금요일").json()
    assert (task["thread_root_id"], task["ref"]["task"]["title"]) == (root_id, "보고서 초안")
    event = reply(app, root_id, "/event 조모임 내일 19:00").json()
    assert event["ref"]["event"]["title"] == "조모임"
    note = reply(app, root_id, "/note 캐시 지역성 정리").json()
    assert note["ref"]["inbox_item"]["suggestion_json"]["type"] == "study_note"

    ask = reply(app, root_id, "/ask 이 계획 괜찮아?").json()
    assert ask["thread_root_id"] == root_id
    deadline = time.time() + 3
    while time.time() < deadline and not any(
        r["author_id"] == "hermes" for r in thread(app, root_id)
    ):
        time.sleep(0.02)
    assert any(r["author_id"] == "hermes" for r in thread(app, root_id))  # answered in the thread
    feed = app.get(f"/api/v1/channels/{course(app)}/messages").json()["items"]
    assert [m["id"] for m in feed] == [root_id]  # nothing leaked to the top level


def test_debate_in_a_thread_reviews_what_is_there(
    app: TestClient, cast: dict[str, Scripted]
) -> None:
    """The main use (user): agents critique and improve a document or code together."""
    draft = "def cache_get(key):\n    return store[key]  # 없으면 KeyError"
    root_id = app.post(f"/api/v1/channels/{course(app)}/messages", json={"body": draft}).json()[
        "id"
    ]
    opened = reply(app, root_id, "/debate @claude @codex 이 함수 개선 --rounds 1")
    assert opened.status_code == 201, opened.text
    debate = opened.json()["ref"]["debate"]
    assert debate["thread_root_id"] == root_id
    done = wait(app, debate["id"], "done")
    first_prompt = cast["claude"].transcripts[0][0].text
    assert "[사용자]: def cache_get(key):" in first_prompt  # the code under review
    assert "/debate" not in first_prompt
    assert "개선안" in cast["hermes"].transcripts[-1][0].text  # summary asks for a final version
    speakers = [r["author_id"] for r in thread(app, root_id) if r["author_type"] == "agent"]
    assert speakers == ["claude", "codex", "hermes"]
    assert done["summary"] is not None


def test_replies_during_a_thread_debate_feed_the_debate(settings: Settings) -> None:
    gate = threading.Event()
    claude = Scripted("claude", ["첫 의견"], gate)
    codex = Scripted("codex", ["둘째 의견"])
    hermes = Scripted("hermes", ["## 요약\n끝"])
    app = make(settings, claude=claude, codex=codex, hermes=hermes)
    try:
        root_id = app.post(
            f"/api/v1/channels/{course(app)}/messages", json={"body": "@claude 초안 봐줘"}
        ).json()["id"]
        time.sleep(0.2)  # claude answers the mention (the gate is open for nobody yet)
        gate.set()
        time.sleep(0.2)
        gate.clear()
        debate = reply(app, root_id, "/debate @claude @codex 초안 개선 --rounds 1").json()["ref"][
            "debate"
        ]
        time.sleep(0.2)
        reply(app, root_id, "예외 처리를 꼭 다뤄 줘")  # would go to sticky claude without a debate
        gate.set()
        wait(app, debate["id"], "done")
        assert "[사용자]: 예외 처리를 꼭 다뤄 줘" in codex.transcripts[0][0].text
        assert claude.calls == 2  # the mention and its debate turn, no extra answer
    finally:
        app.__exit__(None, None, None)
