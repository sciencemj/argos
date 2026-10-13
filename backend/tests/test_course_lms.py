"""A course's LMS material as agent context (PLAN Phase 16d)."""

import time
from collections.abc import Iterator
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from fakes import FakeAgent, FakeClassifier, fake_agents
from fastapi.testclient import TestClient

from argos.config import Settings
from argos.course_lms import LmsDoc, context_section, rank
from argos.main import create_app

SEOUL = ZoneInfo("Asia/Seoul")


def doc(title: str, text: str = "", kind: str = "announcement", day: int = 1) -> LmsDoc:
    return LmsDoc(
        kind=kind,
        title=title,
        text=text,
        url=f"https://lms.example/{title}",
        date=datetime(2026, 10, day, tzinfo=UTC),
        channel_id="c",
    )


def test_rank_prefers_title_matches_and_skips_unrelated() -> None:
    docs = [
        doc("휴강 안내", "다음 주 수업 없음"),
        doc("과제 2", "보고서 제출 형식은 pdf"),
        doc("기타"),
    ]
    found = rank(docs, "과제2 제출 형식이 뭐야?", 5)
    assert [d.title for d in found] == ["과제 2"]
    assert [d.title for d in rank(docs, "", 2)] == ["휴강 안내", "과제 2"]


def test_context_section_cites_and_respects_budget() -> None:
    docs = [doc(f"공지 {i}", "본문 " * 400, day=i + 1) for i in range(30)]
    section = context_section(docs, "공지 3", 2000, SEOUL)
    assert len(section) <= 2000
    assert "### [공지] 공지 3 (2026-10-04)" in section
    assert "https://lms.example/공지 3" in section
    assert "search_course_lms" in section
    assert context_section([], "x", 2000, SEOUL) == ""


@pytest.fixture
def local() -> FakeAgent:
    return FakeAgent("local", ["네"])


@pytest.fixture
def app(settings: Settings, local: FakeAgent) -> Iterator[TestClient]:
    config = settings.model_copy(update={"default_agent": "local"})
    with TestClient(create_app(config)) as client:
        client.app.state.runner.adapter_factory = fake_agents(local=local)  # type: ignore[attr-defined]
        client.app.state.classifier = FakeClassifier(error="no model")  # type: ignore[attr-defined]
        yield client


def test_course_question_gets_its_assignment_and_notices(app: TestClient, local: FakeAgent) -> None:
    token = app.post("/api/v1/lms/connection").json()["token"]
    batch = {
        "account_id": "42",
        "courses": [{"id": "7", "name": "알고리즘"}, {"id": "8", "name": "운영체제"}],
        "items": [
            {
                "id": "10",
                "course_id": "7",
                "kind": "assignment",
                "title": "과제 1 그래프",
                "text": "BFS 구현\n\n제출 안내:\n허용 파일 형식: zip",
                "url": "https://lms.korea.ac.kr/courses/7/assignments/10",
                "due_at": "2026-10-20T14:59:00Z",
            },
            {
                "id": "11",
                "course_id": "7",
                "kind": "announcement",
                "title": "휴강 안내",
                "text": "다음 주 휴강",
                "url": "https://lms.korea.ac.kr/courses/7/discussion_topics/11",
            },
            {
                "id": "12",
                "course_id": "8",
                "kind": "announcement",
                "title": "운영체제 과제 안내",
                "text": "다른 과목",
                "url": "https://lms.korea.ac.kr/courses/8/discussion_topics/12",
            },
        ],
    }
    imported = app.post(
        "/api/v1/lms/import", json=batch, headers={"Authorization": f"Bearer {token}"}
    )
    assert imported.status_code == 200, imported.text
    course = next(
        c["id"] for c in app.get("/api/v1/channels").json()["channels"] if c["name"] == "알고리즘"
    )

    app.post(f"/api/v1/channels/{course}/messages", json={"body": "/ask 과제 1 제출 형식이 뭐야?"})
    deadline = time.time() + 5
    while not local.contexts and time.time() < deadline:
        time.sleep(0.02)

    [context] = local.contexts
    assert "### [과제] 과제 1 그래프 (마감 2026-10-20 23:59)" in context
    assert "허용 파일 형식: zip" in context
    assert "https://lms.korea.ac.kr/courses/7/assignments/10" in context
    assert "휴강 안내" in context
    assert "운영체제 과제 안내" not in context  # another course's notice


def test_activity_repeats_rank_below_the_original() -> None:
    original = doc("과거 시험문제 공개", "시험 범위 안내", day=1)
    repeat = doc("과거 시험문제 공개", "시험 범위 안내", kind="activity", day=2)
    assert rank([repeat, original], "시험 범위", 2) == [original, repeat]
    assert rank([repeat, original], "", 2) == [original, repeat]
    section = context_section([repeat], "", 2000, SEOUL)
    assert "과거 시험문제" not in section  # activity is never listed on its own
