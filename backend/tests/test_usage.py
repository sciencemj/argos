"""Plan usage (PLAN Phase 9): Claude via its status line, Codex via app-server. Both
are unofficial, so every broken or missing source must degrade to "unavailable"."""

import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest
from fakes import FakeAgent, fake_agents
from fastapi.testclient import TestClient

from argos import usage
from argos.config import Settings
from argos.main import create_app

FIXTURES = Path(__file__).parent / "fixtures" / "usage"
CLAUDE_REPLY = json.loads((FIXTURES / "claude_oauth_usage.json").read_text())
TOKEN = "sk-ant-oat01-test-token"


def login(expires_in: timedelta | None = timedelta(hours=1)) -> usage.ClaudeLogin:
    expires = datetime.now(UTC) + expires_in if expires_in is not None else None
    return usage.ClaudeLogin(TOKEN, expires)


def claude(
    transport: httpx2.MockTransport, found: usage.ClaudeLogin | None = None
) -> usage.ProviderUsage:
    provider = usage.ClaudeOAuthUsage(lambda: found if found is not None else login(), transport)
    return asyncio.run(provider.read())


def test_claude_windows_from_the_usage_endpoint() -> None:
    seen: list[httpx2.Request] = []

    def reply(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=CLAUDE_REPLY)

    result = claude(httpx2.MockTransport(reply))
    assert result.state == "ok"
    assert [(w.name, w.used_percent) for w in result.windows] == [("5h", 81.0), ("7d", 78.0)]
    assert result.windows[0].resets_at is not None and result.windows[0].resets_at.year == 2100
    [request] = seen
    assert str(request.url) == usage.CLAUDE_USAGE_URL
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert request.headers["anthropic-beta"] == "oauth-2025-04-20"


@pytest.mark.parametrize(
    ("status", "state", "text"),
    [(401, "unavailable", "만료"), (429, "error", "잠시 뒤"), (500, "error", "500")],
)
def test_claude_refusals_degrade_quietly(status: int, state: str, text: str) -> None:
    result = claude(httpx2.MockTransport(lambda r: httpx2.Response(status)))
    assert (result.state, result.windows) == (state, [])
    assert text in (result.message or "")


def test_claude_network_error_does_not_leak_the_token() -> None:
    def fail(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError(f"cannot reach {request.headers['authorization']}")

    result = claude(httpx2.MockTransport(fail))
    assert result.state == "error" and TOKEN not in (result.message or "")


def test_claude_without_a_usable_login_asks_nothing() -> None:
    def never(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError("no request without a login")

    missing = asyncio.run(usage.ClaudeOAuthUsage(lambda: None, httpx2.MockTransport(never)).read())
    assert missing.state == "unavailable" and "로그인" in (missing.message or "")
    expired = claude(httpx2.MockTransport(never), login(timedelta(minutes=-1)))
    assert "만료" in (expired.message or "")


def test_login_from_the_credentials_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(usage.sys, "platform", "linux")  # no Keychain: the file
    assert usage.read_claude_login(tmp_path) is None
    expires_ms = int(datetime(2100, 1, 1, tzinfo=UTC).timestamp() * 1000)
    (tmp_path / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": TOKEN, "expiresAt": expires_ms}})
    )
    found = usage.read_claude_login(tmp_path)
    assert found is not None and found.token == TOKEN
    assert found.expires_at == datetime(2100, 1, 1, tzinfo=UTC)
    assert usage.parse_credentials('{"claudeAiOauth": {}}') is None
    assert usage.parse_credentials("not json") is None


def test_codex_windows_are_named_by_length_not_position() -> None:
    parsed = usage.parse_codex(
        json.loads((FIXTURES / "codex_ratelimits.json").read_text()), datetime.now(UTC)
    )
    assert {w.name: w.used_percent for w in parsed.windows} == {"7d": 44, "5h": 3}
    assert parsed.plan == "plus"


def test_passed_reset_time_means_no_value() -> None:
    now = datetime.now(UTC)
    stale = usage.ProviderUsage("codex", "ok", [usage.Window("5h", 80, now - timedelta(minutes=1))])
    assert stale.window("5h", now) is None


# --- Codex app-server ----------------------------------------------------------------------------

FAKE_CODEX = r"""#!/usr/bin/env python3
import json, os, sys
reply = json.loads(open(os.environ["FAKE_REPLY"]).read())
for line in sys.stdin:
    msg = json.loads(line)
    if msg.get("id") == 1:
        print(json.dumps({"id": 1, "result": {}}), flush=True)
    elif msg.get("id") == 2:
        print(json.dumps({"id": 2, **reply}), flush=True)
"""


@pytest.fixture
def codex_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    script = tmp_path / "codex"
    script.write_text(FAKE_CODEX)
    script.chmod(0o755)
    return script


def test_codex_app_server_reply(
    codex_bin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reply = tmp_path / "reply.json"
    reply.write_text(
        json.dumps({"result": json.loads((FIXTURES / "codex_ratelimits.json").read_text())})
    )
    monkeypatch.setenv("FAKE_REPLY", str(reply))
    result = asyncio.run(usage.CodexAppServer(str(codex_bin), tmp_path).read())
    assert result.state == "ok" and {w.name for w in result.windows} == {"5h", "7d"}

    reply.write_text(json.dumps({"error": {"code": -32600, "message": "not a ChatGPT login"}}))
    refused = asyncio.run(usage.CodexAppServer(str(codex_bin), tmp_path).read())
    assert (refused.state, refused.windows) == ("unavailable", [])


def test_codex_not_installed(tmp_path: Path) -> None:
    result = asyncio.run(usage.CodexAppServer(str(tmp_path / "nope"), tmp_path).read())
    assert result.state == "unavailable" and "설치" in (result.message or "")


# --- API, status and routing hint ---------------------------------------------------------------


class FixedProvider:
    def __init__(self, name: str, result: usage.ProviderUsage) -> None:
        self.name = name
        self.result = result

    async def read(self) -> usage.ProviderUsage:
        return self.result


@pytest.fixture
def app(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as client:
        monitor = client.app.state.usage  # type: ignore[attr-defined]
        claude_now = usage.parse_claude(CLAUDE_REPLY, datetime.now(UTC))
        codex_now = usage.ProviderUsage("codex", message="Codex CLI가 설치되어 있지 않아요")
        monitor.providers = lambda: {
            "claude": FixedProvider("claude", claude_now),
            "codex": FixedProvider("codex", codex_now),
        }
        yield client


def test_usage_endpoint(app: TestClient) -> None:
    body = app.post("/api/v1/usage/refresh").json()
    by_name = {p["provider"]: p for p in body["providers"]}
    assert by_name["claude"]["state"] == "ok"
    assert by_name["claude"]["windows"][0] == {
        "name": "5h", "used_percent": 81.0, "resets_at": "2100-01-01T00:00:00.188246Z",
    }  # fmt: skip
    assert by_name["codex"]["state"] == "unavailable"
    assert (body["jobs_running"], body["jobs_today"]) == (0, 0)
    assert TOKEN not in app.get("/api/v1/usage").text


def test_nearly_used_up_agent_gets_a_note_in_the_thread(app: TestClient) -> None:
    app.app.state.runner.adapter_factory = fake_agents(claude=FakeAgent("claude", ["네"]))  # type: ignore[attr-defined]
    soon = datetime.now(UTC) + timedelta(minutes=95)
    app.app.state.usage.current["claude"] = usage.ProviderUsage(  # type: ignore[attr-defined]
        "claude", "ok", [usage.Window("5h", 93, soon)]
    )
    channel = next(
        c for c in app.get("/api/v1/channels").json()["channels"] if c["name"] == "컴퓨터구조"
    )
    sent = app.post(
        f"/api/v1/channels/{channel['id']}/messages", json={"body": "@claude 안녕"}
    ).json()
    replies = app.get(f"/api/v1/messages/{sent['id']}/thread").json()["replies"]
    [note] = [r for r in replies if r["author_type"] == "system"]
    assert (
        note["body"].startswith("Claude의 5시간 사용량이 93%예요, 1시간 3")
        and "@codex" in note["body"]
    )
