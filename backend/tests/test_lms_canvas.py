"""Phase 17: LMS sync with a Canvas personal access token, against a fake Canvas."""

import asyncio
import logging
from collections.abc import Iterator
from typing import Any
from zoneinfo import ZoneInfo

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from argos import lms_canvas
from argos.config import Settings
from argos.db import make_engine, make_sessionmaker
from argos.main import create_app
from argos.models import Attachment, Channel, Message, SourceLink, Task

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
                    "updated_at": "2026-10-01T00:00:00Z",
                    "url": f"{ORIGIN}/files/99/download?download_frd=1&verifier=v",
                }
            )
        if path == "/api/v1/files/99/public_url":
            return self.json({"public_url": "https://files.storage.example/b/99?sig=signed-secret"})
        if path.startswith("/api/v1/courses/8/"):
            return self.json([])
        if path in {"/api/v1/users/self/activity_stream", "/api/v1/conversations"}:
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
