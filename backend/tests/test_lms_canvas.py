"""Phase 17: LMS sync with a Canvas personal access token, against a fake Canvas."""

import asyncio
import logging
from collections.abc import Iterator, Sequence
from typing import Any
from zoneinfo import ZoneInfo

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from argos import lms_canvas, lms_match
from argos.config import Settings
from argos.db import make_engine, make_sessionmaker
from argos.main import create_app
from argos.models import Area, Attachment, Channel, LmsNotice, Message, SourceLink, Task

ORIGIN = "https://canvas.school.example"
TOKEN = "7~valid-canvas-token"
PDF = b"%PDF-1.7\nweek one"


class FakeKeychain:
    def __init__(self) -> None:
        self.saved: dict[str, str] = {}

    def get(self, origin: str) -> str | None:
        return self.saved.get(origin)

    def set(self, origin: str, token: str) -> None:
        self.saved[origin] = token

    def delete(self, origin: str) -> None:
        self.saved.pop(origin, None)


class FakeCanvas:
    """The Canvas REST endpoints Argos reads, plus a file store on another host."""

    def __init__(self) -> None:
        self.token = TOKEN
        self.requests: list[httpx.Request] = []
        self.assignment_text = "<p>문제를 풀어 주세요</p><script>x()</script>"
        self.next_link: str | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = request.url
        if url.host == "files.storage.example":
            assert "authorization" not in request.headers
            return httpx.Response(200, content=PDF, headers={"Content-Type": "application/pdf"})
        assert f"{url.scheme}://{url.host}" == ORIGIN
        assert request.method == "GET"
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return httpx.Response(401, json={"errors": [{"message": "Invalid access token."}]})
        path = url.path
        if path == "/api/v1/users/self/profile":
            return self.json({"id": 42, "name": "홍길동", "short_name": "길동"})
        if path == "/api/v1/courses":
            if url.params.get("page") == "2":
                return self.json([{"id": 8, "name": "자료구조"}])
            link = f'<{ORIGIN}/api/v1/courses?page=2&per_page=100>; rel="next"'
            return self.json([{"id": 7, "name": "2026-2 알고리즘(01)"}], link=link)
        if path == "/api/v1/courses/7/assignments":
            return self.json(
                [
                    {
                        "id": 10,
                        "name": "과제 1",
                        "description": self.assignment_text
                        + '<a href="https://forms.example/a?token=secret&x=1">양식</a>',
                        "due_at": "2026-10-20T14:59:00Z",
                        "html_url": f"{ORIGIN}/courses/7/assignments/10",
                        "submission_types": ["online_upload"],
                        "points_possible": 10.0,
                        "submission": {"workflow_state": "unsubmitted"},
                    }
                ],
                link=self.next_link,
            )
        if path == "/api/v1/courses/7/discussion_topics":
            return self.json(
                [
                    {
                        "id": 11,
                        "title": "휴강 안내",
                        "message": "<p>다음 주 휴강</p>",
                        "posted_at": "2026-10-12T01:00:00Z",
                        "html_url": f"{ORIGIN}/courses/7/discussion_topics/11",
                    }
                ]
            )
        if path == "/api/v1/courses/7/modules":
            items = [
                {"id": 500, "type": "File", "title": "1주차 슬라이드", "content_id": 99},
                {"id": 501, "type": "ExternalTool", "title": "녹화 강의"},
            ]
            return self.json([{"id": 1, "name": "1주차", "items_count": 2, "items": items}])
        if path == "/api/v1/courses/7/files/99":
            return self.json(
                {
                    "id": 99,
                    "display_name": "week1.pdf",
                    "size": len(PDF),
                    "created_at": "2026-09-01T03:00:00Z",
                    "updated_at": "2026-10-01T00:00:00Z",
                    "url": f"{ORIGIN}/files/99/download?download_frd=1&verifier=v",
                }
            )
        if path == "/api/v1/files/99/public_url":
            return self.json({"public_url": "https://files.storage.example/b/99?sig=signed-secret"})
        if path.startswith("/api/v1/courses/8/"):
            return self.json([])
        if path == "/api/v1/users/self/activity_stream":
            return self.json(
                [
                    {  # the stream's copy of the announcement above
                        "id": 900,
                        "type": "Announcement",
                        "course_id": 7,
                        "title": "휴강 안내",
                        "message": "<p>다음 주 휴강</p>",
                        "created_at": "2026-10-12T01:00:02Z",
                    },
                    {
                        "id": 901,
                        "type": "Message",
                        "course_id": 7,
                        "title": "Assignment Created - 과제 1",
                        "message": "새 과제",
                        "created_at": "2026-10-10T00:00:00Z",
                    },
                ]
            )
        if path == "/api/v1/conversations":
            return self.json([])
        return httpx.Response(404, json={})

    @staticmethod
    def json(body: Any, link: str | None = None) -> httpx.Response:
        headers = {"Link": link} if link else {}
        return httpx.Response(200, json=body, headers=headers)


@pytest.fixture
def canvas() -> FakeCanvas:
    return FakeCanvas()


@pytest.fixture
def keychain() -> FakeKeychain:
    return FakeKeychain()


@pytest.fixture
def app(settings: Settings, canvas: FakeCanvas, keychain: FakeKeychain) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as client:
        sync = client.app.state.lms_sync  # type: ignore[attr-defined]
        sync.keychain = keychain
        sync.transport = lambda: httpx.MockTransport(canvas)
        yield client


def connect(client: TestClient, token: str = TOKEN) -> httpx.Response:
    return client.put("/api/v1/lms/token", json={"url": "canvas.school.example/", "token": token})


def sync(client: TestClient) -> dict[str, Any]:
    response = client.post("/api/v1/lms/token/sync")
    assert response.status_code == 200, response.text
    return response.json()


def rows(settings: Settings, model: type[Any]) -> list[Any]:
    async def read() -> list[Any]:
        engine = make_engine(settings.db_url)
        async with make_sessionmaker(engine)() as session:
            found = list((await session.scalars(select(model))).all())
        await engine.dispose()
        return found

    return asyncio.run(read())


def test_wrong_token_is_refused_and_not_kept(app: TestClient, keychain: FakeKeychain) -> None:
    response = connect(app, "wrong")
    assert response.status_code == 422
    assert "토큰" in response.json()["error"]["message"]
    assert keychain.saved == {}
    assert app.get("/api/v1/lms/status").json()["token_url"] is None


def test_token_lives_only_in_the_keychain(
    app: TestClient, keychain: FakeKeychain, settings: Settings
) -> None:
    response = connect(app)
    assert response.status_code == 200, response.text
    status = response.json()
    assert status["token_url"] == ORIGIN
    assert status["token_user"] == "길동"
    assert keychain.saved == {ORIGIN: TOKEN}
    assert TOKEN not in settings.db_path.read_bytes().decode("latin-1")
    assert TOKEN not in str(app.get("/api/v1/lms/status").json())
    app.delete("/api/v1/lms/token")
    assert keychain.saved == {}
    assert app.get("/api/v1/lms/status").json()["token_url"] is None


def test_sync_imports_courses_tasks_and_notices_idempotently(
    app: TestClient, settings: Settings, canvas: FakeCanvas
) -> None:
    assert connect(app).status_code == 200
    status = sync(app)
    assert status["token_error"] is None
    assert status["token_last_sync"]
    tasks = rows(settings, Task)
    assert [t.title for t in tasks] == ["과제 1"]
    description = tasks[0].description
    assert "문제를 풀어 주세요" in description and "x()" not in description
    assert "제출 방식: 파일 업로드" in description and "배점: 10점" in description
    assert "secret" not in description and "x=1" in description  # credential params dropped
    assert "마감: 2026. 10. 20. (화) 오후 11:59" in description
    assert any("휴강 안내" in m.body for m in rows(settings, Message))
    names = {c.name for c in rows(settings, Channel)}
    assert {"알고리즘", "자료구조"} <= names  # second page of courses followed
    links = rows(settings, SourceLink)
    assert any(link.container == ORIGIN for link in links if link.object_type == "channel")
    before = (len(rows(settings, Task)), len(rows(settings, Message)))
    assert sync(app)["token_error"] is None
    assert (len(rows(settings, Task)), len(rows(settings, Message))) == before
    # Every Canvas request carried the token to the configured origin only, as GET.
    assert all(r.url.host == "canvas.school.example" for r in canvas.requests)


def test_remote_edit_updates_the_task_but_keeps_local_status(
    app: TestClient, settings: Settings, canvas: FakeCanvas
) -> None:
    connect(app)
    sync(app)
    task = rows(settings, Task)[0]
    assert app.patch(f"/api/v1/tasks/{task.id}", json={"status": "done"}).status_code == 200
    canvas.assignment_text = "<p>범위가 바뀌었어요</p>"
    sync(app)
    [task] = rows(settings, Task)
    assert "범위가 바뀌었어요" in task.description
    assert task.status == "done"


def test_materials_download_without_sending_the_token_to_storage(
    app: TestClient,
    settings: Settings,
    canvas: FakeCanvas,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="httpx2")
    connect(app)
    sync(app)
    assert rows(settings, Attachment) == []  # materials are off by default
    assert app.put("/api/v1/lms/materials", json={"enabled": True}).json()["materials"] is True
    status = sync(app)
    assert status["token_error"] is None, status
    [attachment] = rows(settings, Attachment)
    assert (settings.attachments_dir / attachment.id).read_bytes() == PDF
    storage = [r for r in canvas.requests if r.url.host == "files.storage.example"]
    assert len(storage) == 1
    assert "signed-secret" not in caplog.text
    channel = next(c for c in rows(settings, Channel) if c.name == "알고리즘")
    [listed] = app.get(f"/api/v1/channels/{channel.id}/lms-materials").json()
    assert listed["attachment_id"] == attachment.id
    assert (listed["name"], listed["title"], listed["module"]) == (
        "week1.pdf",
        "1주차 슬라이드",
        "1주차",
    )
    assert listed["posted_at"].startswith("2026-09-01T03:00")
    assert app.get(f"/api/v1/attachments/{attachment.id}/content").content == PDF
    sync(app)  # unchanged file: not downloaded again
    assert len([r for r in canvas.requests if r.url.host == "files.storage.example"]) == 1
    assert len(rows(settings, Attachment)) == 1


def test_revoked_token_reports_and_keeps_imported_data(
    app: TestClient, settings: Settings, canvas: FakeCanvas
) -> None:
    connect(app)
    sync(app)
    canvas.token = "a-new-one"  # the user deleted the token in Canvas
    status = sync(app)
    assert "만료" in status["token_error"]
    assert len(rows(settings, Task)) == 1


def test_link_to_another_host_is_not_followed_with_the_token(
    app: TestClient, canvas: FakeCanvas
) -> None:
    connect(app)
    canvas.next_link = '<https://evil.example/api/v1/steal>; rel="next"'
    status = sync(app)
    assert any("예상하지 못한" in w for w in status["token_warnings"])
    assert all(r.url.host != "evil.example" for r in canvas.requests)


def test_token_settings_are_local_only(app: TestClient) -> None:
    response = app.put(
        "/api/v1/lms/token",
        json={"url": ORIGIN, "token": TOKEN},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert app.post("/api/v1/lms/token/sync").status_code == 409  # nothing connected


def test_idle_sync_reports_no_last_sync_without_a_token(app: TestClient) -> None:
    sync = app.app.state.lms_sync  # type: ignore[attr-defined]
    app.portal.call(sync.run)  # type: ignore[union-attr]  # the timer firing with no token
    assert app.get("/api/v1/lms/status").json()["token_last_sync"] is None


@pytest.mark.parametrize(
    ("raw", "origin"),
    [
        ("lms.school.ac.kr", "https://lms.school.ac.kr"),
        ("https://lms.school.ac.kr/courses/1?x=1", "https://lms.school.ac.kr"),
        ("https://lms.school.ac.kr:8443/", "https://lms.school.ac.kr:8443"),
    ],
)
def test_normalize_url(raw: str, origin: str) -> None:
    assert lms_canvas.normalize_url(raw) == origin


@pytest.mark.parametrize("raw", ["http://lms.school.ac.kr", "https://user:pw@lms.school.ac.kr"])
def test_normalize_url_refuses_unsafe(raw: str) -> None:
    with pytest.raises(lms_canvas.CanvasError):
        lms_canvas.normalize_url(raw)


def test_when_matches_the_extension_format() -> None:
    seoul = ZoneInfo("Asia/Seoul")
    assert lms_canvas.when("2026-10-20T14:59:00Z", seoul) == "2026. 10. 20. (화) 오후 11:59"
    assert lms_canvas.when("2026-10-20T00:05:00Z", seoul) == "2026. 10. 20. (화) 오전 09:05"
    assert lms_canvas.when("2026-10-20T15:30:00Z", seoul) == "2026. 10. 21. (수) 오전 12:30"
    assert lms_canvas.when("nope", seoul) == ""


def test_uninstall_removes_the_token_from_the_keychain(
    app: TestClient, keychain: FakeKeychain
) -> None:
    connect(app)
    removed = app.post("/api/v1/setup/uninstall").json()["removed"]
    assert "키체인의 LMS 액세스 토큰 삭제" in removed
    assert keychain.saved == {}
    assert app.get("/api/v1/lms/status").json()["token_url"] is None


def test_cards_take_the_time_the_lms_posted_them(app: TestClient, settings: Settings) -> None:
    connect(app)
    app.put("/api/v1/lms/materials", json={"enabled": True})
    sync(app)
    created = {
        notice.title: message.created_at
        for notice in rows(settings, LmsNotice)
        for message in rows(settings, Message)
        if message.id == notice.message_id
    }
    assert created["휴강 안내"].isoformat() == "2026-10-12T01:00:00+00:00"
    assert created["1주차 슬라이드"].isoformat() == "2026-09-01T03:00:00+00:00"  # upload time


def user_channel(app: TestClient, settings: Settings, name: str) -> str:
    area = next(a for a in rows(settings, Area) if a.name != "LearningX")
    response = app.post("/api/v1/channels", json={"name": name, "area_id": area.id})
    assert response.status_code == 201, response.text
    channel = response.json()["id"]
    task = app.post("/api/v1/tasks", json={"channel_id": channel, "title": "내 할 일"})
    assert task.status_code == 201, task.text
    return channel


def test_lms_course_adopts_the_users_course_channel(app: TestClient, settings: Settings) -> None:
    mine = user_channel(app, settings, "알고리즘")
    connect(app)
    sync(app)
    named = [c for c in rows(settings, Channel) if c.name.startswith("알고리즘")]
    assert [c.id for c in named] == [mine]  # no "알고리즘 (01)" next to it
    titles = {t.title for t in rows(settings, Task) if t.channel_id == mine}
    assert titles == {"내 할 일", "과제 1"}


def test_split_course_channels_merge_into_the_users_one(
    app: TestClient, settings: Settings
) -> None:
    connect(app)
    sync(app)
    lms = next(c for c in rows(settings, Channel) if c.name == "알고리즘")
    # As before adoption existed: the LMS channel got a section suffix next to the user's.
    renamed = app.patch(f"/api/v1/channels/{lms.id}", json={"name": "알고리즘 (01)"})
    assert renamed.status_code == 200
    mine = user_channel(app, settings, "알고리즘")
    sync(app)
    channels = {c.id for c in rows(settings, Channel)}
    assert lms.id not in channels and mine in channels
    assert {t.title for t in rows(settings, Task) if t.channel_id == mine} == {"내 할 일", "과제 1"}
    assert any("휴강 안내" in m.body for m in rows(settings, Message) if m.channel_id == mine)
    sync(app)  # stable afterwards
    assert {c.id for c in rows(settings, Channel)} == channels


def notice_titles(settings: Settings) -> list[str]:
    return sorted(n.title for n in rows(settings, LmsNotice))


def test_activity_stream_copies_are_skipped(app: TestClient, settings: Settings) -> None:
    connect(app)
    sync(app)
    titles = notice_titles(settings)
    assert titles.count("휴강 안내") == 1
    assert "Assignment Created - 과제 1" in titles  # only the stream tells this


def test_stream_copies_imported_before_are_removed(app: TestClient, settings: Settings) -> None:
    connect(app)
    sync(app)
    code = app.post("/api/v1/lms/connection").json()["token"]
    copy = {  # what an older extension or sync stored
        "account_id": "42",
        "courses": [{"id": "7", "name": "2026-2 알고리즘(01)"}],
        "items": [
            {
                "id": "Announcement:900",
                "course_id": "7",
                "kind": "activity",
                "title": "휴강 안내",
                "text": "다음 주 휴강",
                "url": "https://lms.korea.ac.kr/courses/7",
            }
        ],
    }
    headers = {"Authorization": f"Bearer {code}"}
    assert app.post("/api/v1/lms/import", json=copy, headers=headers).status_code == 200
    assert notice_titles(settings).count("휴강 안내") == 1  # removed in the same import
    sync(app)
    assert notice_titles(settings).count("휴강 안내") == 1


class FakeMatcher:
    def __init__(self, answers: dict[str, str]) -> None:
        self.answers = answers
        self.asked: list[tuple[str, list[str]]] = []

    async def match(self, course: str, channels: Sequence[str]) -> str | None:
        self.asked.append((course, list(channels)))
        return self.answers.get(course)


def with_matcher(app: TestClient, matcher: FakeMatcher) -> None:
    app.app.state.lms_sync.matcher = lambda _settings: matcher  # type: ignore[attr-defined]


def test_ai_match_is_suggested_and_merges_once_approved(
    app: TestClient, settings: Settings
) -> None:
    mine = user_channel(app, settings, "Algorithms")  # named unlike the LMS course
    matcher = FakeMatcher({"2026-2 알고리즘(01)": "Algorithms"})
    with_matcher(app, matcher)
    connect(app)
    sync(app)
    [approval] = app.get("/api/v1/approvals").json()
    assert approval["action"] == "merge_channels" and approval["status"] == "pending"
    lms = approval["payload_json"]["source_channel_id"]
    assert approval["payload_json"]["target_channel_id"] == mine
    assert lms in {c.id for c in rows(settings, Channel)}  # nothing merged yet
    sync(app)
    assert len(matcher.asked) == 2  # each LMS course asked about once (알고리즘, 자료구조)
    assert app.post(f"/api/v1/approvals/{approval['id']}/approve").status_code == 200
    assert lms not in {c.id for c in rows(settings, Channel)}
    assert {t.title for t in rows(settings, Task) if t.channel_id == mine} == {"내 할 일", "과제 1"}
    assert app.get("/api/v1/approvals").json()[0]["status"] == "approved"
    sync(app)  # the course keeps syncing into the user's channel
    assert lms not in {c.id for c in rows(settings, Channel)}


def test_rejected_suggestion_is_not_asked_again(app: TestClient, settings: Settings) -> None:
    user_channel(app, settings, "Algorithms")
    matcher = FakeMatcher({"2026-2 알고리즘(01)": "Algorithms"})
    with_matcher(app, matcher)
    connect(app)
    sync(app)
    [approval] = app.get("/api/v1/approvals").json()
    assert app.post(f"/api/v1/approvals/{approval['id']}/reject").status_code == 200
    sync(app)
    assert len(app.get("/api/v1/approvals").json()) == 1
    assert any(c.name == "알고리즘" for c in rows(settings, Channel))


def test_settings_merge_course_into_a_chosen_channel(app: TestClient, settings: Settings) -> None:
    mine = user_channel(app, settings, "Algorithms")
    with_matcher(app, FakeMatcher({}))
    connect(app)
    status = sync(app)
    assert {t["channel_id"] for t in status["merge_targets"]} >= {mine}
    courses = {c["original"]: c for c in status["courses"]}
    assert courses["알림·메시지"]["course"] is False
    lms = courses["2026-2 알고리즘(01)"]["channel_id"]
    inbox = courses["알림·메시지"]["channel_id"]
    other = courses["자료구조"]["channel_id"] if "자료구조" in courses else None
    refused = app.post(f"/api/v1/lms/courses/{inbox}/merge", json={"target_channel_id": mine})
    assert refused.status_code == 422
    if other:  # an LMS-linked channel is not a target
        taken = app.post(f"/api/v1/lms/courses/{lms}/merge", json={"target_channel_id": other})
        assert taken.status_code == 409
    merged = app.post(f"/api/v1/lms/courses/{lms}/merge", json={"target_channel_id": mine})
    assert merged.status_code == 200, merged.text
    names = {c["channel_id"]: c["name"] for c in merged.json()["courses"]}
    assert names.get(mine) == "Algorithms" and lms not in names
    assert mine not in {t["channel_id"] for t in merged.json()["merge_targets"]}
    sync(app)
    assert {t.title for t in rows(settings, Task) if t.channel_id == mine} == {"내 할 일", "과제 1"}


def test_parse_match_takes_only_a_listed_channel() -> None:
    channels = ["머신러닝", "운영체제"]
    assert lms_match.parse_match("머신러닝", channels) == "머신러닝"
    assert lms_match.parse_match("<think>hmm</think>\n- '머신러닝'", channels) == "머신러닝"
    assert lms_match.parse_match("NONE", channels) is None
    assert lms_match.parse_match("머신 러닝 같아요", channels) is None


def test_a_channel_waiting_on_a_suggestion_is_not_offered_again(
    app: TestClient, settings: Settings
) -> None:
    user_channel(app, settings, "Algorithms")
    # A small model may also pick it for a related course on a later sync.
    matcher = FakeMatcher({"2026-2 알고리즘(01)": "Algorithms", "자료구조": "Algorithms"})
    with_matcher(app, matcher)
    connect(app)
    sync(app)
    sync(app)
    approvals = app.get("/api/v1/approvals").json()
    assert [a["payload_json"]["title"] for a in approvals] == ["알고리즘 → Algorithms"]
