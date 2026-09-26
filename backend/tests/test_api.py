from typing import Any

from fastapi.testclient import TestClient


def course_channel(client: TestClient, name: str = "컴퓨터구조") -> dict[str, Any]:
    channels = client.get("/api/v1/channels").json()["channels"]
    return next(c for c in channels if c["name"] == name)


def test_channels_include_system_and_seeded_courses(client: TestClient) -> None:
    body = client.get("/api/v1/channels").json()
    names = [c["name"] for c in body["channels"]]
    assert names[:2] == ["today", "inbox"]
    assert "컴퓨터구조" in names
    assert [a["name"] for a in body["areas"]] == ["학업", "프로젝트"]


def test_user_adds_and_removes_a_course(client: TestClient) -> None:
    area_id = client.get("/api/v1/channels").json()["areas"][0]["id"]
    created = client.post("/api/v1/channels", json={"name": "자료구조", "area_id": area_id})
    assert created.status_code == 201
    channel = created.json()
    client.post("/api/v1/tasks", json={"channel_id": channel["id"], "title": "x"})

    blocked = client.delete(f"/api/v1/channels/{channel['id']}")
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "conflict"

    forced = client.delete(f"/api/v1/channels/{channel['id']}", params={"force": True})
    assert forced.status_code == 204
    names = [c["name"] for c in client.get("/api/v1/channels").json()["channels"]]
    assert "자료구조" not in names


def test_websocket_receives_object_events(client: TestClient) -> None:
    channel = course_channel(client)
    with client.websocket_connect("/ws") as ws:
        task = client.post(
            "/api/v1/tasks", json={"channel_id": channel["id"], "title": "실시간"}
        ).json()
        created = ws.receive_json()
        client.post(f"/api/v1/tasks/{task['id']}/move", json={"status": "done"})
        updated = ws.receive_json()
        client.delete(f"/api/v1/tasks/{task['id']}")
        deleted = ws.receive_json()

    assert created["type"] == "object.created"
    assert created["data"]["object_type"] == "task"
    assert created["data"]["id"] == task["id"]
    assert updated["type"] == "object.updated"
    assert updated["data"]["object"]["status"] == "done"
    assert deleted["type"] == "object.deleted"
    assert {"type", "data", "ts"} <= created.keys()


def test_config_exposes_wip_limit(client: TestClient) -> None:
    assert client.get("/api/v1/config").json()["wip_limit"] == 3


def test_task_lifecycle_in_course_channel(client: TestClient) -> None:
    channel = course_channel(client)
    created = client.post(
        "/api/v1/tasks",
        json={"channel_id": channel["id"], "title": "과제2", "due_at": "2026-09-26T23:59:00+09:00"},
    )
    assert created.status_code == 201
    task = created.json()
    assert task["due_at"] == "2026-09-26T14:59:00Z"

    listed = client.get("/api/v1/tasks", params={"channel_id": channel["id"]}).json()
    assert [t["id"] for t in listed] == [task["id"]]

    moved = client.post(f"/api/v1/tasks/{task['id']}/move", json={"status": "in_progress"})
    assert moved.json()["status"] == "in_progress"

    patched = client.patch(f"/api/v1/tasks/{task['id']}", json={"title": "과제 2"})
    assert patched.json()["title"] == "과제 2"

    history = client.get(f"/api/v1/tasks/{task['id']}/activity").json()
    assert [h["action"] for h in history] == ["created", "moved", "updated"]

    assert client.delete(f"/api/v1/tasks/{task['id']}").status_code == 204
    assert client.get(f"/api/v1/tasks/{task['id']}").status_code == 404


def test_event_crud_and_range_query(client: TestClient) -> None:
    channel = course_channel(client)
    created = client.post(
        "/api/v1/events",
        json={"channel_id": channel["id"], "title": "중간고사", "start_date": "2026-10-20"},
    )
    assert created.status_code == 201
    event = created.json()
    assert event["all_day"] is True and event["end_date"] == "2026-10-21"

    in_range = client.get(
        "/api/v1/events",
        params={"start": "2026-10-20T00:00:00+09:00", "end": "2026-10-21T00:00:00+09:00"},
    ).json()
    assert [e["id"] for e in in_range] == [event["id"]]

    renamed = client.patch(f"/api/v1/events/{event['id']}", json={"title": "중간 시험"})
    assert renamed.json()["title"] == "중간 시험"
    assert client.delete(f"/api/v1/events/{event['id']}").status_code == 204


def test_inbox_crud_and_today_count(client: TestClient) -> None:
    item = client.post("/api/v1/inbox", json={"raw_text": "금요일까지 과제2"}).json()
    assert item["status"] == "new"
    assert client.get("/api/v1/today").json()["inbox_count"] == 1

    client.patch(f"/api/v1/inbox/{item['id']}", json={"status": "dismissed"})
    assert client.get("/api/v1/today").json()["inbox_count"] == 0

    page = client.get("/api/v1/inbox", params={"status": ["dismissed"]}).json()
    assert [i["id"] for i in page["items"]] == [item["id"]]
    assert page["next_cursor"] is None


def test_personal_space_keeps_capture_and_organised_items_together(client: TestClient) -> None:
    channels = client.get("/api/v1/channels").json()["channels"]
    inbox = next(c for c in channels if c["name"] == "inbox")
    personal = next(c for c in channels if c["kind"] == "personal")

    capture = client.post(
        f"/api/v1/channels/{inbox['id']}/messages", json={"body": "/task 장보기"}
    ).json()
    history = client.post(
        f"/api/v1/channels/{personal['id']}/messages", json={"body": "/event 약속 2026-10-01"}
    ).json()
    assert capture["ref"]["task"]["channel_id"] == personal["id"]
    assert history["ref"]["event"]["channel_id"] == personal["id"]
    task_id = capture["ref"]["task"]["id"]
    event_id = history["ref"]["event"]["id"]
    assert (
        client.patch(f"/api/v1/tasks/{task_id}", json={"channel_id": inbox["id"]}).json()[
            "channel_id"
        ]
        == personal["id"]
    )
    assert (
        client.patch(f"/api/v1/events/{event_id}", json={"channel_id": inbox["id"]}).json()[
            "channel_id"
        ]
        == personal["id"]
    )

    path = f"/api/v1/channels/{inbox['id']}/messages"
    assert [m["id"] for m in client.get(path).json()["items"]] == [capture["id"]]
    merged = client.get(path, params={"include_personal": True}).json()["items"]
    assert {m["id"] for m in merged} == {capture["id"], history["id"]}
    first = client.get(path, params={"include_personal": True, "limit": 1}).json()
    second = client.get(
        path, params={"include_personal": True, "limit": 1, "cursor": first["next_cursor"]}
    ).json()
    assert {first["items"][0]["id"], second["items"][0]["id"]} == {
        capture["id"],
        history["id"],
    }
    assert [
        t["title"]
        for t in client.get("/api/v1/tasks", params={"channel_id": personal["id"]}).json()
    ] == ["장보기"]


def test_errors_use_uniform_shape(client: TestClient) -> None:
    missing = client.get("/api/v1/tasks/nope")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"

    naive = client.post(
        "/api/v1/tasks",
        json={
            "channel_id": course_channel(client)["id"],
            "title": "x",
            "due_at": "2026-09-26T23:59:00",
        },
    )
    assert naive.status_code == 422
    assert naive.json()["error"]["code"] == "validation_error"

    bad_event = client.post(
        "/api/v1/events", json={"channel_id": course_channel(client)["id"], "title": "x"}
    )
    assert bad_event.status_code == 422
    assert bad_event.json()["error"]["code"] == "invalid"

    task = client.post(
        "/api/v1/tasks", json={"channel_id": course_channel(client)["id"], "title": "x"}
    ).json()
    cleared = client.patch(f"/api/v1/tasks/{task['id']}", json={"status": None})
    assert cleared.status_code == 422
    assert cleared.json()["error"]["code"] == "invalid"

    unknown_route = client.get("/api/v1/nothing")
    assert unknown_route.json() == {"error": {"code": "http_error", "message": "Not Found"}}


def test_routine_api(client: TestClient) -> None:
    created = client.post("/api/v1/routines", json={"title": "아침 운동", "weekdays": "0123456"})
    routine_id = created.json()["id"]
    today = client.get("/api/v1/routines").json()
    [routine] = today["routines"]
    assert (routine["title"], routine["done"], routine["streak"]) == ("아침 운동", False, 0)

    checked = client.put(
        f"/api/v1/routines/{routine_id}/checks/{today['today']}", json={"done": True}
    )
    assert checked.status_code == 204
    [routine] = client.get("/api/v1/routines").json()["routines"]
    assert (routine["done"], routine["streak"]) == (True, 1)

    future = client.put(f"/api/v1/routines/{routine_id}/checks/2999-01-01", json={"done": True})
    assert future.status_code == 422

    client.patch(f"/api/v1/routines/{routine_id}", json={"title": "운동 30분"})
    assert client.get("/api/v1/routines").json()["routines"][0]["title"] == "운동 30분"
    assert client.delete(f"/api/v1/routines/{routine_id}").status_code == 204
    assert client.get("/api/v1/routines").json()["routines"] == []


def test_personal_channel_listed(client: TestClient) -> None:
    channels = client.get("/api/v1/channels").json()["channels"]
    [personal] = [c for c in channels if c["kind"] == "personal"]
    assert personal["name"] == "일상"
    assert client.delete(f"/api/v1/channels/{personal['id']}").status_code == 422
