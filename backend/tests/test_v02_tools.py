"""v0.2 tools: render_artifact, web_fetch (SSRF guard), ask_agent (delegation limits and
cancellation) and local folders linked to a channel."""

import asyncio
import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fakes import TERMINAL, FakeAgent, FakeClassifier
from fastapi.testclient import TestClient
from test_harness import ScriptedLLM, call
from test_harness import _course as _course  # pyright: ignore[reportPrivateUsage]
from test_harness import _wait as _wait  # pyright: ignore[reportPrivateUsage]

from argos import extra_tools, services
from argos.config import Settings
from argos.harness import (
    HarnessRun,
    LLMDone,
    NativeHarnessAdapter,
    ToolContext,
    ToolDef,
    decide,
)
from argos.main import create_app
from argos.models import Agent, PermissionMode

# --- render_artifact -------------------------------------------------------------------------


async def test_render_artifact_validates_and_stays_short(tmp_path: Path) -> None:
    ctx = ToolContext(workspace=tmp_path, agent="a")
    out = json.loads(
        await extra_tools.render_artifact(
            {"title": "Chart", "kind": "svg", "code": "<svg></svg>"}, ctx
        )
    )
    assert out == {"rendered": True, "title": "Chart", "kind": "svg", "chars": 11}
    with pytest.raises(ValueError, match="kind"):
        await extra_tools.render_artifact({"title": "x", "kind": "pdf", "code": "x"}, ctx)
    with pytest.raises(ValueError, match="비어"):
        await extra_tools.render_artifact({"title": "x", "kind": "html", "code": " "}, ctx)


# --- web_fetch -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ip",
    ["127.0.0.1", "10.1.2.3", "172.16.0.9", "192.168.1.1", "169.254.169.254", "100.100.1.1",
     "::1", "fe80::1", "::ffff:127.0.0.1", "0.0.0.0", "224.0.0.1"],
)  # fmt: skip
def test_blocked_ips(ip: str) -> None:
    assert extra_tools.blocked_ip(ip)


def test_public_ips_pass() -> None:
    assert not extra_tools.blocked_ip("93.184.216.34")
    assert not extra_tools.blocked_ip("2606:2800:220:1:248:1893:25c8:1946")


def resolver(table: dict[str, list[str]]) -> Any:
    async def resolve(host: str, port: int) -> list[str]:
        if host not in table:
            raise OSError("no such host")
        return table[host]

    return resolve


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("file:///etc/passwd", "http"),
        ("ftp://example.com/x", "http"),
        ("http://localhost:8100/api", "내부"),
        ("http://argos.local/", "내부"),
        ("http://127.0.0.1/", "사설"),
        ("http://[::1]/", "사설"),
        ("http://rebind.test/", "사설"),  # a public name that resolves privately
        ("http://user:pw@example.test/", "계정"),
        ("http://nowhere.test/", "찾을 수 없어요"),
    ],
)
async def test_check_url_refuses(url: str, reason: str) -> None:
    resolve = resolver({"example.test": ["93.184.216.34"], "rebind.test": ["10.0.0.5"]})
    with pytest.raises(extra_tools.FetchBlocked, match=reason):
        await extra_tools.check_url(url, resolve)


async def test_fetch_follows_redirects_but_rechecks_them() -> None:
    pages = {
        "http://example.test/start": httpx2.Response(
            302, headers={"location": "http://example.test/page"}
        ),
        "http://example.test/page": httpx2.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text="<html><head><title>Hi</title><script>x()</script></head>"
            "<body><h1>Title</h1><p>Hello <b>world</b></p></body></html>",
        ),
        "http://example.test/evil": httpx2.Response(
            301, headers={"location": "http://169.254.169.254/latest/meta-data"}
        ),
    }
    transport = httpx2.MockTransport(lambda request: pages[str(request.url)])
    resolve = resolver({"example.test": ["93.184.216.34"]})
    final, kind, raw = await extra_tools.fetch_page(
        "http://example.test/start", resolve=resolve, transport=transport
    )
    assert (final, kind) == ("http://example.test/page", "text/html")
    assert extra_tools.html_to_text(raw) == ("Hi", "Title\nHello world")
    with pytest.raises(extra_tools.FetchBlocked):
        await extra_tools.fetch_page(
            "http://example.test/evil", resolve=resolve, transport=transport
        )


# --- linked folders --------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Not under tmp_path: the test database lives there, and that folder is refused.
    home = tmp_path_factory.mktemp("home")
    (home / "notes" / "sub").mkdir(parents=True)
    (home / ".ssh").mkdir()
    (home / "Library" / "Prefs").mkdir(parents=True)

    def fake_home(cls: type[Path]) -> Path:
        return home

    monkeypatch.setattr(Path, "home", classmethod(fake_home))
    monkeypatch.setenv("HOME", str(home))  # what expanduser reads
    return home


def test_check_linked_folder(home: Path, tmp_path: Path) -> None:
    data = home / "argos-data"
    data.mkdir()
    db = data / "argos.db"
    check = services.check_linked_folder
    assert check("~/notes", data, db) == str((home / "notes").resolve())
    assert check(str(home / "notes" / "sub"), data, db) == str((home / "notes" / "sub").resolve())
    bad = {
        "notes": "절대 경로",
        "~": "너무 넓은",
        "~/.ssh": "숨김",
        "~/Library/Prefs": "시스템",
        "~/missing": "없는 폴더",
        str(tmp_path): "홈 폴더나 외장",
        "~/argos-data": "Argos 데이터",
    }
    for raw, reason in bad.items():
        with pytest.raises(services.InvalidError, match=reason):
            check(raw, data, db)
    (home / "link").symlink_to(home / ".ssh")
    with pytest.raises(services.InvalidError, match="숨김"):
        check("~/link", data, db)


def test_linked_folders_count_as_workspace(tmp_path: Path) -> None:
    linked = tmp_path / "linked"
    linked.mkdir()
    write = ToolDef(
        "write_file",
        "w",
        {"type": "object"},
        "write",
        extra_tools.render_artifact,
        paths=lambda a: [str(a["path"])],
    )
    args = {"path": str(linked / "a.md")}
    assert decide(write, args, PermissionMode.AUTO_EDIT, tmp_path / "ws") == "ask"
    assert decide(write, args, PermissionMode.AUTO_EDIT, tmp_path / "ws", (linked,)) == "allow"
    ctx = ToolContext(workspace=tmp_path / "ws", agent="a", trusted=(linked,))
    assert linked in ctx.allowed_roots()


# --- through the app: folders API and ask_agent ------------------------------------------------


@pytest.fixture
def app(settings: Settings, home: Path) -> Iterator[TestClient]:
    config = settings.model_copy(update={"max_agent_depth": 2})
    with TestClient(create_app(config)) as client:
        client.app.state.classifier = FakeClassifier(error="no model")  # type: ignore[attr-defined]
        yield client


def test_folders_api(app: TestClient, home: Path) -> None:
    channel = _course(app)
    url = f"/api/v1/channels/{channel}/folders"
    linked = app.post(url, json={"path": "~/notes"})
    assert linked.status_code == 200, linked.text
    assert linked.json()["linked_folders"] == [str((home / "notes").resolve())]
    assert app.post(url, json={"path": "~/.ssh"}).status_code == 422
    remote = app.post(url, json={"path": "~/notes"}, headers={"Tailscale-User-Login": "me@x"})
    assert remote.status_code in (403, 401)  # never from a phone through serve
    gone = app.delete(url, params={"path": str((home / "notes").resolve())})
    assert gone.json()["linked_folders"] is None
    assert app.delete(url, params={"path": "/nope"}).status_code == 404


def _factory(scripts: dict[str, list[ScriptedLLM]], fakes: dict[str, FakeAgent]) -> Any:
    def factory(
        agent: Agent,
        settings: Settings,
        sessions: Any = None,
        job_workspace: Any = None,
        no_tools: bool = False,
        coding: bool = False,
        harness: HarnessRun | None = None,
    ) -> Any:
        if agent.name in fakes:
            return fakes[agent.name]
        assert harness is not None
        ctx = ToolContext(
            workspace=settings.argos_workspace, agent=agent.name, delegate=harness.delegate
        )
        tools = {t.name: t for t in extra_tools.EXTRA_TOOLS}
        from argos.harness import ToolRegistry

        return NativeHarnessAdapter(
            scripts[agent.name].pop(0),
            agent.name,
            ToolRegistry([tools["ask_agent"]]),
            ctx,
            harness.gate,
        )

    return factory


def _native(app: TestClient, name: str) -> None:
    made = app.post(
        "/api/v1/agents",
        json={"name": name, "display_name": name, "backend": "native", "model": "m",
              "tools": ["ask_agent"]},
    )  # fmt: skip
    assert made.status_code == 201, made.text


def _ask(app: TestClient, channel: str, body: str) -> dict[str, Any]:
    sent = app.post(f"/api/v1/channels/{channel}/messages", json={"body": body})
    assert sent.status_code == 201, sent.text
    return sent.json()


def _steps_of(app: TestClient, root: str, agent: str) -> tuple[dict[str, Any], list[Any]]:
    def done() -> dict[str, Any] | None:
        thread = app.get(f"/api/v1/messages/{root}/thread").json()
        mine = [
            r
            for r in thread["replies"]
            if r["author_id"] == agent and r["run"] and r["run"]["status"] in TERMINAL
        ]
        return mine[0] if mine else None

    reply = _wait(done, 10)
    return reply, app.get(f"/api/v1/runs/{reply['run']['id']}/steps").json()


def test_ask_agent_delegates_and_reports_back(app: TestClient) -> None:
    _native(app, "boss")
    helper = FakeAgent("local", ["4", "2"])
    scripts = {
        "boss": [
            ScriptedLLM(
                [
                    LLMDone("", [call("c1", "ask_agent", '{"agent": "local", "prompt": "6*7?"}')]),
                    LLMDone("42래요"),
                ]
            )
        ]
    }
    app.app.state.runner.adapter_factory = _factory(scripts, {"local": helper})  # type: ignore[attr-defined]
    channel = _course(app)
    root = _ask(app, channel, "@boss 계산 좀 맡겨줘")
    reply, steps = _steps_of(app, root["id"], "boss")
    assert reply["body"] == "42래요"
    result = next(s for s in steps if s["kind"] == "tool_result")
    assert result["output"] == "42" and not result["is_error"]
    # The helper's question and answer are in the same thread, as a child run.
    thread = app.get(f"/api/v1/messages/{root['id']}/thread").json()["replies"]
    asked = next(r for r in thread if r["author_id"] == "boss" and r["body"].startswith("@local"))
    assert asked["body"] == "@local 6*7?"
    child = next(r for r in thread if r["author_id"] == "local")
    assert child["body"] == "42" and child["run"]["kind"] == "delegate"
    assert helper.transcripts[0][-1].speaker == "boss"


def test_ask_agent_refuses_cycles_and_depth(app: TestClient, settings: Settings) -> None:
    _native(app, "boss")
    _native(app, "mid")
    scripts = {
        # boss asks itself: a cycle
        "boss": [
            ScriptedLLM(
                [
                    LLMDone("", [call("c1", "ask_agent", '{"agent": "boss", "prompt": "hi"}')]),
                    LLMDone("ok"),
                ]
            ),
            # boss -> mid -> local (depth 2, allowed) and mid -> boss would be a cycle
            ScriptedLLM(
                [
                    LLMDone("", [call("c2", "ask_agent", '{"agent": "mid", "prompt": "go"}')]),
                    LLMDone("done"),
                ]
            ),
        ],
        "mid": [
            ScriptedLLM(
                [
                    LLMDone("", [call("c3", "ask_agent", '{"agent": "boss", "prompt": "back"}')]),
                    LLMDone("mid done"),
                ]
            )
        ],
    }
    app.app.state.runner.adapter_factory = _factory(scripts, {})  # type: ignore[attr-defined]
    channel = _course(app)
    first = _ask(app, channel, "@boss 혼자 해봐")
    _, steps = _steps_of(app, first["id"], "boss")
    result = next(s for s in steps if s["kind"] == "tool_result")
    assert result["is_error"] and "순환" in result["output"]

    second = _ask(app, channel, "@boss 위임해봐")
    _, mid_steps = _steps_of(app, second["id"], "mid")
    mid_result = next(s for s in mid_steps if s["kind"] == "tool_result")
    assert mid_result["is_error"] and "순환" in mid_result["output"]


def test_ask_agent_depth_limit(settings: Settings, home: Path) -> None:
    config = settings.model_copy(update={"max_agent_depth": 1})
    with TestClient(create_app(config)) as app:
        app.app.state.classifier = FakeClassifier(error="no model")  # type: ignore[attr-defined]
        _native(app, "boss")
        _native(app, "mid")
        scripts = {
            "boss": [
                ScriptedLLM(
                    [
                        LLMDone("", [call("c1", "ask_agent", '{"agent": "mid", "prompt": "x"}')]),
                        LLMDone("done"),
                    ]
                )
            ],
            "mid": [
                ScriptedLLM(
                    [
                        LLMDone("", [call("c2", "ask_agent", '{"agent": "local", "prompt": "y"}')]),
                        LLMDone("mid done"),
                    ]
                )
            ],
        }
        app.app.state.runner.adapter_factory = _factory(  # type: ignore[attr-defined]
            scripts, {"local": FakeAgent("local", ["z"])}
        )
        root = _ask(app, _course(app), "@boss 깊게")
        _, mid_steps = _steps_of(app, root["id"], "mid")
        result = next(s for s in mid_steps if s["kind"] == "tool_result")
        assert result["is_error"] and "1단계" in result["output"]


def test_cancelling_the_parent_cancels_the_child(app: TestClient) -> None:
    _native(app, "boss")
    hold = asyncio.Event()  # the helper never finishes on its own
    helper = FakeAgent("local", ["thinking", "more"], hold=hold)
    scripts = {
        "boss": [
            ScriptedLLM(
                [
                    LLMDone("", [call("c1", "ask_agent", '{"agent": "local", "prompt": "slow"}')]),
                    LLMDone("never"),
                ]
            )
        ]
    }
    app.app.state.runner.adapter_factory = _factory(scripts, {"local": helper})  # type: ignore[attr-defined]
    channel = _course(app)
    root = _ask(app, channel, "@boss 오래 걸리는 일")

    def runs() -> dict[str, dict[str, Any]] | None:
        thread = app.get(f"/api/v1/messages/{root['id']}/thread").json()["replies"]
        found = {r["author_id"]: r["run"] for r in thread if r["run"]}
        return found if {"boss", "local"} <= set(found) else None

    started = _wait(runs)
    assert app.post(f"/api/v1/runs/{started['boss']['id']}/cancel").json() == {"cancelled": True}
    deadline = time.time() + 15
    while time.time() < deadline:
        now = runs() or {}
        if all(now.get(a, {}).get("status") == "cancelled" for a in ("boss", "local")):
            break
        time.sleep(0.05)
    final = runs() or {}
    assert final["boss"]["status"] == "cancelled"
    assert final["local"]["status"] == "cancelled"
