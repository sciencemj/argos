"""MCP over real HTTP: a uvicorn server on a free port and the SDK client."""

import asyncio
import json
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import httpx2
import pytest
import uvicorn
from mcp.client import Client

from argos.config import Settings
from argos.main import create_app


@pytest.fixture
def server(settings: Settings) -> Iterator[str]:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = uvicorn.Config(
        create_app(settings), host="127.0.0.1", port=port, log_level="warning", ws="none"
    )
    uv = uvicorn.Server(config)
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not uv.started:
        if time.time() > deadline:
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    uv.should_exit = True
    thread.join(timeout=5)


def call(base: str, tool: str, args: dict[str, Any] | None = None, agent: str = "claude") -> Any:
    async def go() -> Any:
        async with Client(f"{base}/mcp?agent={agent}") as client:
            result: Any = await client.call_tool(tool, args or {})
            if result.is_error:
                return {"error": "".join(c.text for c in result.content)}
            # Lists come back as structured {"result": [...]}; dicts as one JSON text block.
            if result.structured_content is not None:
                return result.structured_content.get("result", result.structured_content)
            return json.loads(result.content[0].text) if result.content else None

    return asyncio.run(go())


def rest(base: str, method: str, path: str, **kw: Any) -> Any:
    response = httpx2.request(method, f"{base}/api/v1{path}", **kw)
    return response.json() if response.content else None


def test_tool_list(server: str) -> None:
    async def go() -> list[str]:
        async with Client(f"{server}/mcp") as client:
            return [t.name for t in (await client.list_tools()).tools]

    names = asyncio.run(go())
    assert set(names) == {
        "list_channels", "get_today", "get_schedule", "list_tasks", "list_inbox",
        "get_course_progress", "add_task", "update_task", "move_task", "create_event",
        "update_event", "capture_note", "delete_task", "delete_event", "search_notes",
    }  # fmt: skip


def test_add_task_is_attributed_and_announced(server: str) -> None:
    task = call(
        server, "add_task", {"title": "과제2 제출", "channel": "#컴퓨터구조", "due": "2026-09-26"}
    )
    assert task["due_at"] == "2026-09-26T23:59:00+09:00"
    assert task["channel"] == "컴퓨터구조"

    [log] = rest(server, "GET", f"/tasks/{task['id']}/activity")
    assert log["actor"] == "agent:claude"

    channels = rest(server, "GET", "/channels")["channels"]
    course = next(c for c in channels if c["name"] == "컴퓨터구조")
    [message] = rest(server, "GET", f"/channels/{course['id']}/messages")["items"]
    assert (message["author_type"], message["author_id"]) == ("agent", "claude")
    assert message["ref"]["task"]["id"] == task["id"]

    listed = call(server, "list_tasks", {"channel": "컴퓨터구조"})
    assert [t["title"] for t in listed] == ["과제2 제출"]
    moved = call(server, "move_task", {"task_id": task["id"], "status": "in_progress"})
    assert moved["status"] == "in_progress"
    progress = call(server, "get_course_progress", {"channel": "컴퓨터구조"})
    assert progress[0]["counts"]["in_progress"] == 1


def test_delete_needs_approval(server: str) -> None:
    task = call(server, "add_task", {"title": "지울 할 일", "channel": "일상"})
    asked = call(
        server, "delete_task", {"task_id": task["id"], "reason": "중복이에요"}, agent="hermes"
    )
    assert asked["status"] == "pending"
    assert rest(server, "GET", f"/tasks/{task['id']}")["title"] == "지울 할 일"  # still there

    [pending] = rest(server, "GET", "/approvals", params={"status": "pending"})
    assert (pending["action"], pending["requested_by"]) == ("delete_task", "agent:hermes")
    approved = rest(server, "POST", f"/approvals/{pending['id']}/approve")
    assert approved["status"] == "approved"
    assert httpx2.get(f"{server}/api/v1/tasks/{task['id']}").status_code == 404
    log = rest(server, "GET", f"/tasks/{task['id']}/activity")
    assert log[-1]["action"] == "deleted" and log[-1]["actor"] == "agent:hermes"

    kept = call(server, "add_task", {"title": "남길 할 일", "channel": "일상"})
    call(server, "delete_task", {"task_id": kept["id"]})
    [second] = rest(server, "GET", "/approvals", params={"status": "pending"})
    assert rest(server, "POST", f"/approvals/{second['id']}/reject")["status"] == "rejected"
    assert rest(server, "GET", f"/tasks/{kept['id']}")["title"] == "남길 할 일"
    again = httpx2.post(f"{server}/api/v1/approvals/{second['id']}/approve")
    assert again.status_code == 409


def test_events_and_schedule(server: str) -> None:
    timed = call(
        server,
        "create_event",
        {"title": "조교 면담", "channel": "인공지능", "start": "2026-09-29T15:00"},
    )
    assert (timed["starts_at"], timed["ends_at"]) == (
        "2026-09-29T15:00:00+09:00",
        "2026-09-29T16:00:00+09:00",
    )
    trip = call(
        server,
        "create_event",
        {"title": "MT", "channel": "일상", "start": "2026-10-03", "end": "2026-10-04"},
    )
    assert (trip["start_date"], trip["end_date"]) == ("2026-10-03", "2026-10-04")

    week = call(server, "get_schedule", {"start": "2026-09-28", "end": "2026-10-05"})
    assert {e["title"] for e in week} == {"조교 면담", "MT"}
    moved = call(server, "update_event", {"event_id": timed["id"], "start": "2026-09-30T10:00"})
    assert moved["starts_at"] == "2026-09-30T10:00:00+09:00"

    asked = call(server, "delete_event", {"event_id": trip["id"]})
    assert asked["status"] == "pending"


def test_errors_are_tool_errors(server: str) -> None:
    assert "not found" in call(server, "add_task", {"title": "x", "channel": "없는채널"})["error"]
    bad = call(server, "add_task", {"title": "x", "channel": "일상", "status": "doing"})
    assert "status must be" in bad["error"]
    assert "error" in call(
        server, "create_event", {"title": "x", "channel": "일상", "start": "금요일"}
    )


def test_capture_note_and_today(server: str) -> None:
    captured = call(server, "capture_note", {"text": "다음주에 치과 가기"}, agent="codex")
    assert captured["status"] == "new"
    [item] = call(server, "list_inbox")
    assert item["text"] == "다음주에 치과 가기"
    today = call(server, "get_today")
    assert today["open_inbox"] == 1
    assert set(today) >= {"date", "events", "due_tasks", "routines"}


def test_agent_header_and_unknown(server: str) -> None:
    async def go(headers: dict[str, str], query: str) -> str:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        async with (
            httpx2.AsyncClient(headers=headers) as http,
            streamable_http_client(f"{server}/mcp{query}", http_client=http) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result: Any = await session.call_tool("add_task", {"title": "헤더", "channel": "일상"})
            return json.loads(result.content[0].text)["id"]

    by_header = asyncio.run(go({"X-Argos-Agent": "Hermes"}, ""))
    assert rest(server, "GET", f"/tasks/{by_header}/activity")[0]["actor"] == "agent:hermes"
    anonymous = asyncio.run(go({}, "?agent=not%20ok!"))
    assert rest(server, "GET", f"/tasks/{anonymous}/activity")[0]["actor"] == "agent:unknown"


def test_rejects_foreign_host_header(server: str) -> None:
    """DNS-rebinding guard: a page on another origin cannot drive the local MCP server."""
    response = httpx2.post(
        f"{server}/mcp",
        headers={"Host": "evil.example", "Content-Type": "application/json"},
        content=b"{}",
    )
    assert response.status_code in (400, 403, 421)
