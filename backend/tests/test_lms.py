import asyncio
import copy
import json
import logging
from typing import Any, cast

import httpx2 as httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from argos import lms, services
from argos.config import Settings
from argos.models import ActivityLog, AppSetting, SourceLink, Task


def batch() -> dict[str, Any]:
    return {
        "account_id": "42",
        "courses": [{"id": "7", "name": "알고리즘"}],
        "items": [
            {
                "id": "10",
                "course_id": "7",
                "kind": "assignment",
                "title": "과제 1",
                "text": "문제를 풀어 주세요",
                "url": "https://lms.korea.ac.kr/courses/7/assignments/10",
                "due_at": "2026-10-20T14:59:00Z",
            },
            {
                "id": "11",
                "course_id": "7",
                "kind": "announcement",
                "title": "공지",
                "text": "[링크](javascript:alert(1))",
                "url": "https://lms.korea.ac.kr/courses/7/discussion_topics/11",
            },
        ],
    }


def token(client: TestClient) -> str:
    response = client.post("/api/v1/lms/connection")
    assert response.status_code == 200
    return response.json()["token"]


def test_pair_rotation_revocation_and_cross_site(client: TestClient) -> None:
    assert client.get("/api/v1/lms/status").json()["connected"] is False
    assert client.post("/api/v1/lms/import", json=batch()).status_code == 401
    first = token(client)
    second = token(client)
    for value, expected in [(first, 401), (second, 200)]:
        assert (
            client.post(
                "/api/v1/lms/import", json=batch(), headers={"Authorization": f"Bearer {value}"}
            ).status_code
            == expected
        )
    assert (
        client.post(
            "/api/v1/lms/connection", headers={"Origin": "https://lms.korea.ac.kr"}
        ).status_code
        == 403
    )
    assert (
        client.post("/api/v1/lms/connection", headers={"Host": "attacker.example"}).status_code
        == 403
    )
    assert client.delete("/api/v1/lms/connection").json()["connected"] is False
    assert (
        client.post(
            "/api/v1/lms/import", json=batch(), headers={"Authorization": f"Bearer {second}"}
        ).status_code
        == 401
    )


def test_import_idempotency_remote_updates_and_local_status(client: TestClient) -> None:
    secret = token(client)
    headers = {"Authorization": f"Bearer {secret}"}
    payload = batch()
    result = client.post("/api/v1/lms/import", json=payload, headers=headers)
    assert result.status_code == 200, result.text
    assert result.json() == {"created": 2, "updated": 0, "unchanged": 0}
    task = next(t for t in client.get("/api/v1/tasks").json() if t["title"] == "과제 1")
    client.patch(f"/api/v1/tasks/{task['id']}", json={"status": "done"})
    assert client.post("/api/v1/lms/import", json=payload, headers=headers).json()["unchanged"] == 2
    payload["items"][0]["title"] = "과제 수정"
    payload["items"][0]["due_at"] = None
    assert client.post("/api/v1/lms/import", json=payload, headers=headers).json()["updated"] == 1
    updated = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert updated["title"] == "과제 수정"
    assert updated["status"] == "done"
    assert updated["due_at"] is None
    # A partial fetch must not erase a task missing from this batch.
    payload["items"] = []
    assert client.post("/api/v1/lms/import", json=payload, headers=headers).status_code == 200
    assert client.get(f"/api/v1/tasks/{task['id']}").status_code == 200
    assert client.get("/api/v1/lms/status").json()["last_sync"]


def test_validation_does_not_write_and_account_ids_are_separate(client: TestClient) -> None:
    headers = {"Authorization": f"Bearer {token(client)}"}
    for mutation in ("url", "unknown_course", "duplicate", "naive_date"):
        payload = batch()
        if mutation == "url":
            payload["items"][0]["url"] = "https://attacker.example/"
        elif mutation == "unknown_course":
            payload["items"][0]["course_id"] = "999"
        elif mutation == "duplicate":
            payload["items"].append(copy.deepcopy(payload["items"][0]))
        else:
            payload["items"][0]["due_at"] = "2026-10-10T12:00:00"
        assert client.post("/api/v1/lms/import", json=payload, headers=headers).status_code == 422
    assert not any(t["title"] == "과제 1" for t in client.get("/api/v1/tasks").json())
    for account in ("42", "43"):
        payload = batch()
        payload["account_id"] = account
        assert (
            client.post("/api/v1/lms/import", json=payload, headers=headers).json()["created"] == 2
        )
    tasks = [t for t in client.get("/api/v1/tasks").json() if t["title"] == "과제 1"]
    assert len(tasks) == 2
    assert tasks[0]["channel_id"] != tasks[1]["channel_id"]


def test_pair_secret_is_hashed_and_not_logged(client: TestClient) -> None:
    secret = token(client)

    async def inspect() -> None:
        async with cast(FastAPI, client.app).state.sessionmaker() as session:
            saved = await services.get_setting(session, lms.TOKEN_KEY)
            assert saved and secret not in saved
            logs = (await session.scalars(select(ActivityLog))).all()
            assert not any(secret in str(log.after_json) for log in logs)

    asyncio.run(inspect())


def test_import_logs_and_plain_text(client: TestClient) -> None:
    headers = {"Authorization": f"Bearer {token(client)}"}
    client.post("/api/v1/lms/import", json=batch(), headers=headers)

    async def inspect() -> None:
        from argos.models import Message

        async with cast(FastAPI, client.app).state.sessionmaker() as session:
            links = (
                await session.scalars(select(SourceLink).where(SourceLink.source == "learningx"))
            ).all()
            assert len(links) == 4  # two channels and two items
            messages = (
                await session.scalars(select(Message).where(Message.author_type == "system"))
            ).all()
            assert any(r"\[링크\]" in message.body for message in messages)
            logs = (
                await session.scalars(select(ActivityLog).where(ActivityLog.actor == "system:lms"))
            ).all()
            assert any(log.object_type == "task" for log in logs)
            tasks = (await session.scalars(select(Task).where(Task.title == "과제 1"))).all()
            assert len(tasks) == 1
            assert await session.scalar(
                select(AppSetting.value).where(AppSetting.key == "lms_last_sync")
            )

    asyncio.run(inspect())


def material_batch(data: bytes) -> dict[str, Any]:
    payload = batch()
    payload["items"] = [
        {
            "id": "50",
            "course_id": "7",
            "kind": "material",
            "title": "1주차 자료",
            "text": "주차/단원: 1주차",
            "url": "https://lms.korea.ac.kr/courses/7/modules/items/50",
            "file": {
                "id": "100",
                "name": "../lecture.pdf",
                "size": len(data),
                "updated_at": "2026-10-08T07:00:00Z",
            },
        }
    ]
    return payload


def upload_material(
    client: TestClient, headers: dict[str, str], payload: dict[str, Any], data: bytes
):
    return client.post(
        "/api/v1/lms/materials/file",
        headers=headers,
        data={
            "metadata": json.dumps(
                {"account_id": payload["account_id"], "item": payload["items"][0]}
            )
        },
        files={"file": ("untrusted-name.pdf", data)},
    )


def test_material_bytes_idempotency_updates_and_missing_file_recovery(
    client: TestClient, settings: Settings
) -> None:
    from argos.models import Attachment

    data = b"%PDF-1.7\noriginal"
    payload = material_batch(data)
    headers = {"Authorization": f"Bearer {token(client)}"}
    assert client.post("/api/v1/lms/import", json=payload, headers=headers).status_code == 200
    assert client.post("/api/v1/lms/materials/needed", json=payload, headers=headers).json()[
        "item_ids"
    ] == ["50"]
    assert upload_material(client, headers, payload, data).json()["created"] == 1
    assert (
        client.post("/api/v1/lms/materials/needed", json=payload, headers=headers).json()[
            "item_ids"
        ]
        == []
    )
    assert upload_material(client, headers, payload, data).json()["unchanged"] == 1

    async def attached() -> tuple[str, str]:
        async with cast(FastAPI, client.app).state.sessionmaker() as session:
            items = (await session.scalars(select(Attachment))).all()
            assert len(items) == 1
            assert items[0].message_id
            assert items[0].name == "lecture.pdf"
            return items[0].id, items[0].message_id

    first_id, message_id = asyncio.run(attached())
    assert client.get(f"/api/v1/attachments/{first_id}/content").content == data
    payload["items"][0]["title"] = "1주차 자료 (제목 수정)"
    assert client.post("/api/v1/lms/import", json=payload, headers=headers).status_code == 200
    assert client.post("/api/v1/lms/materials/needed", json=payload, headers=headers).json() == {
        "item_ids": []
    }
    assert asyncio.run(attached())[0] == first_id
    updated = b"%PDF-1.7\nupdated version"
    previous = copy.deepcopy(payload)
    payload["items"][0]["file"].update(size=len(updated), updated_at="2026-10-08T08:00:00Z")
    assert client.post("/api/v1/lms/import", json=payload, headers=headers).status_code == 200
    assert upload_material(client, headers, previous, data).status_code == 409
    assert upload_material(client, headers, payload, b"incomplete").status_code == 422
    assert client.get(f"/api/v1/attachments/{first_id}/content").content == data
    assert len(list(settings.attachments_dir.iterdir())) == 1
    assert upload_material(client, headers, payload, updated).json()["updated"] == 1
    second_id, same_message = asyncio.run(attached())
    assert same_message == message_id and second_id != first_id
    assert not (settings.attachments_dir / first_id).exists()
    assert client.get(f"/api/v1/attachments/{second_id}/content").content == updated
    (settings.attachments_dir / second_id).unlink()
    assert client.post("/api/v1/lms/materials/needed", json=payload, headers=headers).json()[
        "item_ids"
    ] == ["50"]
    assert upload_material(client, headers, payload, updated).status_code == 200
    assert len(list(settings.attachments_dir.iterdir())) == 1


def test_material_invalid_uploads_leave_no_bytes(client: TestClient, settings: Settings) -> None:
    data = b"%PDF-1.7\noriginal"
    payload = material_batch(data)
    headers = {"Authorization": f"Bearer {token(client)}"}
    assert upload_material(client, {}, payload, data).status_code == 401
    assert upload_material(client, headers, payload, data).status_code == 409
    assert client.post("/api/v1/lms/import", json=payload, headers=headers).status_code == 200
    assert upload_material(client, headers, payload, b"short").status_code == 422
    html = b"<!doctype html><html>login</html>"
    payload["items"][0]["file"]["size"] = len(html)
    assert client.post("/api/v1/lms/import", json=payload, headers=headers).status_code == 200
    assert upload_material(client, headers, payload, html).status_code == 422
    assert not list(settings.attachments_dir.glob("*"))
    payload["items"][0]["file"]["size"] = 26 * 1024 * 1024
    assert client.post("/api/v1/lms/import", json=payload, headers=headers).status_code == 422


def test_signed_storage_download_has_no_credentials_and_is_not_persisted(
    client: TestClient,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="httpx2")
    data = b"%PDF-1.7\noriginal"
    payload = material_batch(data)
    headers = {"Authorization": f"Bearer {token(client)}"}
    assert client.post("/api/v1/lms/import", json=payload, headers=headers).status_code == 200
    url = "https://kr.object.gov-ncloudstorage.com/korea-canvas-contents/account_1/tmp/nas_files/100/lecture.pdf?X-Amz-Signature=temporary-secret"
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert "cookie" not in request.headers
        assert "authorization" not in request.headers
        return httpx.Response(200, content=data, headers={"Content-Type": "application/pdf"})

    real = httpx.AsyncClient

    def make_client(**kwargs: Any) -> httpx.AsyncClient:
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return real(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(lms.httpx, "AsyncClient", make_client)
    body = {"account_id": "42", "item": payload["items"][0], "download_url": url}
    assert client.post("/api/v1/lms/materials/download", json=body).status_code == 401
    assert not seen
    assert (
        client.post("/api/v1/lms/materials/download", json=body, headers=headers).json()["created"]
        == 1
    )
    assert (
        client.post("/api/v1/lms/materials/download", json=body, headers=headers).json()[
            "unchanged"
        ]
        == 1
    )
    assert len(seen) == 1
    assert "kr.object.gov-ncloudstorage.com" in caplog.text
    assert "temporary-secret" not in caplog.text
    assert next(settings.attachments_dir.iterdir()).read_bytes() == data

    async def inspect() -> None:
        async with cast(FastAPI, client.app).state.sessionmaker() as session:
            logs = (await session.scalars(select(ActivityLog))).all()
            links = (await session.scalars(select(SourceLink))).all()
            assert "temporary-secret" not in str([log.after_json for log in logs])
            assert "temporary-secret" not in str([link.container for link in links])

    asyncio.run(inspect())


def test_storage_download_rejects_unknown_urls_redirects_and_bad_bytes(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = b"%PDF-1.7\noriginal"
    payload = material_batch(data)
    headers = {"Authorization": f"Bearer {token(client)}"}
    client.post("/api/v1/lms/import", json=payload, headers=headers)
    prefix = "https://kr.object.gov-ncloudstorage.com/korea-canvas-contents/100/file.pdf"
    body = {"account_id": "42", "item": payload["items"][0], "download_url": prefix}
    status_code, content = 302, b""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            status_code,
            content=content,
            headers={"Location": "http://127.0.0.1/", "Content-Type": "application/pdf"},
        )

    real = httpx.AsyncClient

    def make_client(**kwargs: Any) -> httpx.AsyncClient:
        return real(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(lms.httpx, "AsyncClient", make_client)
    for invalid in [
        "http://127.0.0.1/file",
        "https://kr.object.gov-ncloudstorage.com.attacker.example/korea-canvas-contents/100/file",
        "https://kr.object.gov-ncloudstorage.com/other-bucket/100/file",
        "https://kr.object.gov-ncloudstorage.com/korea-canvas-contents/999/file",
        "https://kr.object.gov-ncloudstorage.com/korea-canvas-contents/%2e%2e/100/file",
    ]:
        body["download_url"] = invalid
        assert (
            client.post("/api/v1/lms/materials/download", json=body, headers=headers).status_code
            == 422
        )
    assert not seen
    body["download_url"] = prefix
    assert (
        client.post("/api/v1/lms/materials/download", json=body, headers=headers).status_code == 502
    )
    assert len(seen) == 1
    status_code, content = 200, data + b"extra"
    assert (
        client.post("/api/v1/lms/materials/download", json=body, headers=headers).status_code == 413
    )
    content = b"truncated"
    assert (
        client.post("/api/v1/lms/materials/download", json=body, headers=headers).status_code == 422
    )
    assert not list(settings.attachments_dir.glob("*"))


def lms_channels(client: TestClient) -> dict[str, str]:
    courses = client.get("/api/v1/lms/status").json()["courses"]
    return {course["original"]: course["name"] for course in courses}


def test_course_names_are_cleaned_and_toggle_renames_only_automatic_names(
    client: TestClient,
) -> None:
    headers = {"Authorization": f"Bearer {token(client)}"}
    payload = batch()
    payload["courses"] = [
        {"id": "7", "name": "262R (서울-학부)컴파일러(COMPILERS)-02분반"},
        {"id": "8", "name": "262R (서울-학부)컴파일러(COMPILERS)-01분반"},
        {"id": "9", "name": "[2026-1] 자료구조 (01)"},
    ]
    assert client.post("/api/v1/lms/import", json=payload, headers=headers).status_code == 200
    assert client.get("/api/v1/lms/status").json()["clean_course_names"] is True
    assert lms_channels(client) == {
        "262R (서울-학부)컴파일러(COMPILERS)-02분반": "컴파일러",
        "262R (서울-학부)컴파일러(COMPILERS)-01분반": "컴파일러 (01분반)",
        "[2026-1] 자료구조 (01)": "자료구조",
        "알림·메시지": "알림·메시지",
    }
    # A name the user chose survives the toggle and later syncs.
    channel = next(
        c for c in client.get("/api/v1/channels").json()["channels"] if c["name"] == "자료구조"
    )
    client.patch(f"/api/v1/channels/{channel['id']}", json={"name": "자구"})

    off = client.put("/api/v1/lms/course-names", json={"enabled": False})
    assert off.status_code == 200
    assert off.json()["clean_course_names"] is False
    names = lms_channels(client)
    assert names["[2026-1] 자료구조 (01)"] == "자구"
    assert names["262R (서울-학부)컴파일러(COMPILERS)-02분반"].startswith(
        "262R (서울-학부)컴파일러(COMPILERS)-02분반 · LMS "
    )

    client.put("/api/v1/lms/course-names", json={"enabled": True})
    payload["courses"][0]["name"] = "262R (서울-학부)컴파일러2(COMPILERS)-02분반"
    client.post("/api/v1/lms/import", json=payload, headers=headers)
    names = lms_channels(client)
    assert names["262R (서울-학부)컴파일러2(COMPILERS)-02분반"] == "컴파일러2"
    assert names["[2026-1] 자료구조 (01)"] == "자구"


def test_course_name_toggle_is_local_only(client: TestClient) -> None:
    response = client.put(
        "/api/v1/lms/course-names",
        json={"enabled": False},
        headers={"Origin": "https://lms.korea.ac.kr"},
    )
    assert response.status_code == 403
