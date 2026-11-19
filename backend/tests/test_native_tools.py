"""v0.2 Stage 1.2: the Argos tools in the native registry, workspace file/shell tools,
tool plugins, channel file uploads and the knowledge index."""

import json
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from fakes import FakeClassifier
from fastapi.testclient import TestClient
from files import text_pdf
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from argos import argos_tools, file_tools, knowledge, services
from argos.agents import ApprovalNeeded, ToolResult, Turn
from argos.config import Settings
from argos.db import make_engine, make_sessionmaker
from argos.harness import (
    HMsg,
    LLMDone,
    NativeHarnessAdapter,
    PendingCall,
    ToolContext,
    ToolDef,
    ToolRegistry,
    decide,
)
from argos.main import create_app
from argos.models import Attachment, Channel, PermissionMode, SourceLink, Task
from argos.plugins import load_plugins


@pytest.fixture
async def maker(settings: Settings) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = make_engine(settings.db_url)
    yield make_sessionmaker(engine)
    await engine.dispose()


async def _channel(maker: async_sessionmaker[AsyncSession], name: str = "알고리즘") -> Channel:
    async with maker() as s:
        areas = await services.list_areas(s)
        area = (
            areas[0]
            if areas
            else await services.create_area(s, name="학업", icon=None, actor="user")
        )
        return await services.create_channel(
            s, name=name, area_id=area.id, kind=services.ChannelKind.COURSE, actor="user"
        )


def argos_registry() -> ToolRegistry:
    registry = ToolRegistry()
    argos_tools.register_argos_tools(registry)
    return registry


def ctx_for(
    settings: Settings, maker: Any, workspace: Path | None = None, **extra: Any
) -> ToolContext:
    return ToolContext(
        workspace=workspace or settings.argos_workspace,
        agent="nat",
        sessionmaker=maker,
        settings=settings,
        **extra,
    )


# --- Argos tools ----------------------------------------------------------------------------


def test_every_argos_tool_is_shared() -> None:
    registry = argos_registry()
    names = set(registry.names())
    assert {"add_task", "delete_task", "search_knowledge", "list_channels"} <= names
    assert registry.get("delete_task").kind == "destructive"  # type: ignore[union-attr]
    assert registry.get("delete_event").kind == "destructive"  # type: ignore[union-attr]
    assert registry.get("list_tasks").kind == "read"  # type: ignore[union-attr]
    schema = registry.get("add_task").parameters  # type: ignore[union-attr]
    assert set(schema["required"]) == {"title", "channel"}
    assert "ctx" not in schema["properties"]


async def test_argos_tools_through_the_native_handler(
    settings: Settings, maker: async_sessionmaker[AsyncSession]
) -> None:
    channel = await _channel(maker)
    registry = argos_registry()
    ctx = ctx_for(settings, maker)
    add = registry.get("add_task")
    assert add is not None
    out = json.loads(await add.handler({"title": "과제 3", "channel": channel.name}, ctx))
    assert out["title"] == "과제 3" and out["channel"] == channel.name
    with pytest.raises(ValueError, match="인자 오류"):
        await add.handler({"channel": channel.name}, ctx)  # no title
    async with maker() as s:
        task = await s.scalar(select(Task).where(Task.title == "과제 3"))
        assert task is not None
    listed = json.loads(await registry.get("list_tasks").handler({}, ctx))  # type: ignore[union-attr]
    assert [t["title"] for t in listed] == ["과제 3"]


class ScriptedLLM:
    pdf_native = False

    def __init__(self, turns: list[LLMDone]) -> None:
        self.turns = turns

    async def turn(
        self, system: str, history: list[HMsg], tools: list[ToolDef]
    ) -> AsyncIterator[Any]:
        yield self.turns.pop(0)


class Gate:
    def __init__(self, answer: bool) -> None:
        self.answer = answer

    async def request(self, tool: ToolDef, args: dict[str, Any], summary: str) -> str:
        return "ap"

    async def wait(self, approval_id: str) -> bool:
        return self.answer


@pytest.mark.parametrize("approve", [True, False])
async def test_delete_task_waits_inline_then_deletes(
    settings: Settings, maker: async_sessionmaker[AsyncSession], approve: bool
) -> None:
    channel = await _channel(maker)
    async with maker() as s:
        task = await services.create_task(s, channel_id=channel.id, title="지울 것", actor="user")
    llm = ScriptedLLM(
        [
            LLMDone("", [PendingCall("c1", "delete_task", json.dumps({"task_id": task.id}))]),
            LLMDone("끝"),
        ]
    )
    adapter = NativeHarnessAdapter(
        llm, "nat", argos_registry(), ctx_for(settings, maker), Gate(approve)
    )
    events = [e async for e in adapter.stream([Turn("user", "지워")], "sys", "s")]
    approval = next(e for e in events if isinstance(e, ApprovalNeeded))
    assert approval.summary.startswith("할 일 삭제")
    result = next(e for e in events if isinstance(e, ToolResult))
    assert result.is_error is (not approve)
    async with maker() as s:
        assert (await s.get(Task, task.id) is None) is approve


# --- file and shell tools --------------------------------------------------------------------


@pytest.fixture
def ws(tmp_path: Path) -> ToolContext:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    other = tmp_path / "project"
    other.mkdir()
    return ToolContext(
        workspace=workspace, agent="nat", roots=(other,), backup_dir=tmp_path / "backups"
    )


def files() -> ToolRegistry:
    registry = ToolRegistry()
    file_tools.register_file_tools(registry)
    return registry


async def call(name: str, args: dict[str, Any], ctx: ToolContext) -> str:
    tool = files().get(name)
    assert tool is not None
    return await tool.handler(args, ctx)


async def test_write_read_edit_with_backups(ws: ToolContext) -> None:
    assert "bytes" in await call("write_file", {"path": "a/b.txt", "content": "one\ntwo\n"}, ws)
    assert await call("read_file", {"path": "a/b.txt"}, ws) == "     1\tone\n     2\ttwo"
    assert "2\ttwo" in await call("read_file", {"path": "a/b.txt", "offset": 2}, ws)
    await call("edit_file", {"path": "a/b.txt", "old_string": "two", "new_string": "2"}, ws)
    assert (ws.workspace / "a/b.txt").read_text() == "one\n2\n"
    with pytest.raises(ValueError, match="없어요"):
        await call("edit_file", {"path": "a/b.txt", "old_string": "zzz", "new_string": "x"}, ws)
    await call("write_file", {"path": "a/b.txt", "content": "x x"}, ws)
    with pytest.raises(ValueError, match="2곳"):
        await call("edit_file", {"path": "a/b.txt", "old_string": "x", "new_string": "y"}, ws)
    await call(
        "edit_file",
        {"path": "a/b.txt", "old_string": "x", "new_string": "y", "replace_all": True},
        ws,
    )
    assert (ws.workspace / "a/b.txt").read_text() == "y y"
    assert ws.backup_dir is not None
    kept = sorted(p.read_text() for p in (ws.backup_dir / "manual").iterdir())
    assert kept == ["one\n2\n", "one\ntwo\n", "x x"]


async def test_list_glob_grep(ws: ToolContext) -> None:
    (ws.workspace / "src").mkdir()
    (ws.workspace / "src" / "main.py").write_text("print('hello')\n# TODO fix\n")
    (ws.workspace / "notes.md").write_text("todo: read\n")
    assert await call("list_dir", {}, ws) == "src/\nnotes.md\t11"
    assert await call("glob_files", {"pattern": "*.py"}, ws) == "src/main.py"
    hits = await call("grep_files", {"pattern": "todo", "ignore_case": True}, ws)
    assert hits.splitlines() == ["notes.md:1: todo: read", "src/main.py:2: # TODO fix"]
    only_py = await call("grep_files", {"pattern": "todo", "ignore_case": True, "glob": "*.py"}, ws)
    assert only_py == "src/main.py:2: # TODO fix"


async def test_paths_stay_inside_the_roots(ws: ToolContext, tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("key")
    with pytest.raises(file_tools.OutsideRoots):
        await call("read_file", {"path": str(secret)}, ws)
    with pytest.raises(file_tools.OutsideRoots):
        await call("read_file", {"path": "../secret.txt"}, ws)
    os.symlink(tmp_path, ws.workspace / "escape")  # a link out of the workspace
    with pytest.raises(file_tools.OutsideRoots):
        await call("read_file", {"path": "escape/secret.txt"}, ws)
    with pytest.raises(file_tools.OutsideRoots):
        await call("write_file", {"path": "escape/new.txt", "content": "x"}, ws)
    # Another allowed root (the channel's project folder) is reachable.
    (tmp_path / "project" / "p.txt").write_text("ok")
    assert "ok" in await call("read_file", {"path": str(tmp_path / "project" / "p.txt")}, ws)
    with pytest.raises(PermissionError):
        await call("delete_file", {"path": "."}, ws)


async def test_run_command_output_exit_code_and_timeout(ws: ToolContext) -> None:
    out = await call("run_command", {"command": "echo hi; echo err >&2; exit 3"}, ws)
    assert out.splitlines() == ["exit code 3", "hi", "err"]
    (ws.workspace / "sub").mkdir()
    assert (await call("run_command", {"command": "pwd", "cwd": "sub"}, ws)).endswith("/sub")
    slow = await call("run_command", {"command": "sleep 5; echo never", "timeout": 1}, ws)
    assert slow.startswith("1초가 지나 멈췄어요") and "never" not in slow
    big = await call("run_command", {"command": "yes | head -c 100000"}, ws)
    assert "생략" in big and len(big) < file_tools.MAX_COMMAND_OUTPUT + 200


def test_file_tool_permissions(ws: ToolContext, tmp_path: Path) -> None:
    registry = files()
    auto, ask = PermissionMode.AUTO_EDIT, PermissionMode.ASK

    def d(name: str, args: dict[str, Any], mode: PermissionMode = auto) -> str:
        tool = registry.get(name)
        assert tool is not None
        return decide(tool, args, mode, ws.workspace)

    assert d("read_file", {"path": "a.txt"}) == "allow"
    assert d("write_file", {"path": "a.txt", "content": ""}) == "allow"
    assert d("write_file", {"path": "a.txt", "content": ""}, ask) == "ask"
    assert d("read_file", {"path": str(tmp_path / "project" / "p.txt")}) == "ask"  # outside
    assert d("run_command", {"command": "ls"}) == "ask"
    assert d("delete_file", {"path": "a.txt"}) == "ask"
    assert d("list_dir", {}, ask) == "allow"


# --- plugins -----------------------------------------------------------------------------------

PLUGIN = """
from argos.harness import ToolDef

async def weather(args, ctx):
    return "맑음 " + args.get("city", "")

def register(registry):
    registry.register(ToolDef("weather", "날씨", {"type": "object"}, "read", weather))
"""


async def test_plugins_load_stamp_and_roll_back(tmp_path: Path) -> None:
    folder = tmp_path / "plugins"
    folder.mkdir()
    (folder / "wx.py").write_text(PLUGIN)
    (folder / "broken.py").write_text(
        PLUGIN.replace('"weather"', '"weather2"').replace("def register", "def register_x")
        + "\ndef register(registry):\n"
        "    registry.register(ToolDef('half', 'x', {}, 'read', weather))\n"
        "    raise RuntimeError('boom')\n"
    )
    (folder / "clash.py").write_text(PLUGIN.replace('"weather"', '"read_file"'))
    (folder / "_private.py").write_text("raise SystemExit")
    registry = files()
    statuses = {s.name: s for s in load_plugins(folder, registry)}
    assert set(statuses) == {"wx", "broken", "clash"}
    assert statuses["wx"].tools == ["weather"] and statuses["wx"].error is None
    tool = registry.get("weather")
    assert tool is not None and (tool.source, tool.plugin) == ("plugin:wx", "wx")
    assert await tool.handler({"city": "서울"}, ToolContext(Path("."), "x")) == "맑음 서울"
    assert "boom" in (statuses["broken"].error or "") and "half" not in registry
    assert "already exists" in (statuses["clash"].error or "")
    assert registry.get("read_file").source == "core"  # type: ignore[union-attr]
    (folder / "wx.py").unlink()
    load_plugins(folder, registry)  # reload: the removed plugin's tools go away
    assert "weather" not in registry
    assert load_plugins(tmp_path / "missing", registry) == []


# --- channel files and knowledge ---------------------------------------------------------------


@pytest.fixture
def app(settings: Settings) -> Iterator[TestClient]:
    (settings.data_dir / "plugins").mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "plugins" / "wx.py").write_text(PLUGIN)
    with TestClient(create_app(settings)) as client:
        client.app.state.classifier = FakeClassifier(error="no model")  # type: ignore[attr-defined]
        yield client


def course(app: TestClient) -> str:
    return next(
        c["id"] for c in app.get("/api/v1/channels").json()["channels"] if c["kind"] == "course"
    )


def test_channel_files_upload_search_delete(app: TestClient, settings: Settings) -> None:
    channel = course(app)
    files_url = f"/api/v1/channels/{channel}/files"
    up = app.post(files_url, files={"file": ("강의 1.pdf", text_pdf("Dijkstra shortest path"))})
    assert up.status_code == 201, up.text
    assert up.json()["path"] == "강의 1.pdf" and up.json()["indexed"] is True
    again = app.post(
        files_url,
        files={"file": ("강의 1.pdf", b"notes about heaps")},
        data={"folder": "week2"},
    )
    assert again.json()["path"] == "week2/강의 1.pdf"
    dup = app.post(files_url, files={"file": ("강의 1.pdf", b"x")})
    assert dup.json()["path"] == "강의 1 (2).pdf"
    listed = {f["path"] for f in app.get(files_url).json()}
    assert listed == {"강의 1.pdf", "week2/강의 1.pdf", "강의 1 (2).pdf"}
    stored = knowledge.uploads_dir(settings.argos_workspace, channel) / "강의 1.pdf"
    assert stored.is_file()

    escape = app.post(files_url, files={"file": ("x.txt", b"x")}, data={"folder": "../../x"})
    assert escape.status_code == 422
    assert app.delete(files_url, params={"path": "../../../test.db"}).status_code == 422
    assert app.delete(files_url, params={"path": "nope.txt"}).status_code == 404

    tools = {t["name"]: t for t in app.get("/api/v1/agents/tools").json()}
    assert tools["read_file"]["native_only"] is True and tools["run_command"]["kind"] == "approval"
    assert tools["weather"]["source"] == "plugin:wx"
    assert app.get("/api/v1/plugins").json()[0]["tools"] == ["weather"]

    assert app.delete(files_url, params={"path": "week2"}).status_code == 204
    assert {f["path"] for f in app.get(files_url).json()} == {"강의 1.pdf", "강의 1 (2).pdf"}


async def test_search_knowledge_tool(
    settings: Settings, maker: async_sessionmaker[AsyncSession]
) -> None:
    channel = await _channel(maker)
    other = await _channel(maker, "자료구조")
    root = knowledge.uploads_dir(settings.argos_workspace, channel.id)
    root.mkdir(parents=True)
    (root / "summary.md").write_text("# 정리\n다익스트라 알고리즘은 우선순위 큐를 쓴다")
    (root / "image.bin").write_bytes(b"\x00\x01\x02")
    async with maker() as s:
        await knowledge.index_upload(s, settings.argos_workspace, channel.id, root / "summary.md")
        await knowledge.index_upload(s, settings.argos_workspace, channel.id, root / "image.bin")
        # An LMS material: an attachment on a message in the other course.
        message = await services.create_message(s, channel_id=other.id, body="자료", actor="system")
        settings.attachments_dir.mkdir(parents=True, exist_ok=True)
        attachment = Attachment(
            message_id=message.id, name="week3.pdf", mime="application/pdf", size=0, kind="pdf"
        )
        s.add(attachment)
        await s.flush()
        data = text_pdf("red black tree rotation")
        (settings.attachments_dir / attachment.id).write_bytes(data)
        attachment.size = len(data)
        s.add(
            SourceLink(
                source="learningx",
                external_id="m1",
                uid="m1",
                object_type="attachment",
                object_id=attachment.id,
                container="https://lms.test/m1",
                container_name="week3.pdf",
            )
        )
        await s.commit()
        assert await knowledge.index_lms_materials(s, settings.attachments_dir) == 1
        assert await knowledge.index_lms_materials(s, settings.attachments_dir) == 0  # unchanged

    search = argos_registry().get("search_knowledge")
    assert search is not None
    ctx = ctx_for(settings, maker)
    hits = json.loads(await search.handler({"query": "우선순위 큐"}, ctx))
    assert [(h["title"], h["path"], h["channel"]) for h in hits] == [
        ("summary.md", "uploads/summary.md", "알고리즘")
    ]
    assert "[우선순위 큐]" in hits[0]["snippet"]
    lms = json.loads(await search.handler({"query": "rotation", "channel": "자료구조"}, ctx))
    assert [(h["title"], h["source"]) for h in lms] == [("week3.pdf", "lms")]
    none = json.loads(await search.handler({"query": "rotation", "channel": "알고리즘"}, ctx))
    assert none == []
    short = json.loads(await search.handler({"query": "큐"}, ctx))
    assert [h["title"] for h in short] == ["summary.md"]
    async with maker() as s:
        await knowledge.remove_upload(s, channel.id, "summary.md")
    assert json.loads(await search.handler({"query": "우선순위"}, ctx)) == []


async def test_plugin_links_out_of_the_folder_are_skipped(tmp_path: Path) -> None:
    folder = tmp_path / "plugins"
    folder.mkdir()
    outside = tmp_path / "evil.py"
    outside.write_text(PLUGIN)
    os.symlink(outside, folder / "evil.py")
    registry = ToolRegistry()
    [status] = load_plugins(folder, registry)
    assert status.error and "weather" not in registry


async def test_every_waiter_on_an_approval_wakes() -> None:
    import asyncio

    from argos.approval_waits import ApprovalWaits

    waits = ApprovalWaits()

    async def pending() -> bool | None:
        return None

    first = asyncio.create_task(waits.wait("a", pending, poll=10))
    second = asyncio.create_task(waits.wait("a", pending, poll=10))
    await asyncio.sleep(0)
    waits.settle("a", True)
    assert await first is True and await second is True
    assert waits.waiting() == []


def test_mcp_serves_every_destructive_tool_as_an_approval_request() -> None:
    from fastapi import FastAPI

    from argos import mcp_server

    mcp_server.build_mcp(FastAPI())
    assert mcp_server.DESTRUCTIVE == {"delete_task", "delete_event"}
    assert mcp_server.DESTRUCTIVE <= set(mcp_server.TOOL_NAMES)
