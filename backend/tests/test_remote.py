"""Phase 14: opening Argos on the phone through Tailscale serve — the CLI wrapper (with a
fake CLI fed real `--json` output shapes) and the gate on requests that come through it."""

import copy
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from argos import remote
from argos.config import Settings
from argos.main import create_app

FIXTURES = Path(__file__).parent / "fixtures" / "tailscale"
OWNER = "owner@example.com"
HOST = "macbook.example-tailnet.ts.net"


class FakeTailscale:
    """Answers `status --json`, `serve status --json` and `serve --bg --set-path …`."""

    def __init__(self) -> None:
        self.status: dict[str, Any] = json.loads((FIXTURES / "status.json").read_text())
        self.serve: dict[str, Any] = json.loads((FIXTURES / "serve-status.json").read_text())
        self.calls: list[list[str]] = []
        self.https_off = False

    @property
    def handlers(self) -> dict[str, Any]:
        return self.serve["Web"][f"{HOST}:443"]["Handlers"]

    def __call__(self, args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        cmd = args[1:]
        self.calls.append(cmd)
        out = ""
        if cmd == ["status", "--json"]:
            out = json.dumps(self.status)
        elif cmd == ["serve", "status", "--json"]:
            out = json.dumps(self.serve)
        elif cmd[:3] == ["serve", "--bg", "--set-path"]:
            path, target = cmd[3], cmd[4]
            if target == "off":
                self.handlers.pop(path, None)
            elif self.https_off:
                raise subprocess.TimeoutExpired(
                    args,
                    timeout,
                    output="Serve is not enabled on your tailnet.\nTo enable, visit:\n\n"
                    "         https://login.tailscale.com/f/serve?node=nABC\n",
                )
            else:
                self.handlers[path] = {"Proxy": target}
        return subprocess.CompletedProcess(args, 0, out, "")


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeTailscale:
    tailscale = FakeTailscale()
    monkeypatch.setattr(remote, "_run", tailscale)
    return tailscale


@pytest.fixture
def config(settings: Settings) -> Settings:
    return settings.model_copy(update={"tailscale_bin": "tailscale-fake"})


@pytest.fixture
def app_client(config: Settings, fake: FakeTailscale) -> Iterator[TestClient]:
    with TestClient(create_app(config)) as c:
        yield c


def test_without_tailscale(client: TestClient) -> None:
    out = client.get("/api/v1/remote").json()
    assert out["installed"] is False and out["enabled"] is False
    assert "Tailscale" in out["problem"]


def test_logged_out(app_client: TestClient, fake: FakeTailscale) -> None:
    fake.status["BackendState"] = "NeedsLogin"
    out = app_client.post("/api/v1/remote").json()
    assert out["running"] is False and out["enabled"] is False
    assert not any(c[:2] == ["serve", "--bg"] for c in fake.calls)


def test_turning_on_serves_our_path_and_allows_this_macs_account(
    app_client: TestClient, fake: FakeTailscale
) -> None:
    before = app_client.get("/api/v1/remote").json()
    assert before["login"] == OWNER and before["host"] == HOST and before["enabled"] is False

    out = app_client.post("/api/v1/remote").json()
    assert ["serve", "--bg", "--set-path", "/argos", "http://127.0.0.1:8000/argos"] in fake.calls
    assert out["enabled"] is True and out["proxy"] == "http://127.0.0.1:8000/argos"
    assert out["url"] == f"https://{HOST}/argos/"
    assert out["allowed"] == [OWNER]
    assert "/other" in fake.handlers  # other services on the same address stay

    off = app_client.delete("/api/v1/remote").json()
    assert off["enabled"] is False and "/other" in fake.handlers
    assert off["allowed"] == [OWNER]  # kept for the next time


def test_https_not_enabled_shows_tailscales_link(
    app_client: TestClient, fake: FakeTailscale
) -> None:
    fake.https_off = True
    out = app_client.post("/api/v1/remote").json()
    assert out["enabled"] is False
    assert out["enable_link"] == "https://login.tailscale.com/f/serve?node=nABC"
    assert "HTTPS" in out["problem"]


def test_settings_are_saved_and_cleaned(app_client: TestClient) -> None:
    out = app_client.put(
        "/api/v1/remote/settings",
        json={
            "allowed": [" Owner@Example.com ", "owner@example.com", "", "phone@x.io"],
            "autostart": True,
        },
    ).json()
    assert out["allowed"] == [OWNER, "phone@x.io"] and out["autostart"] is True
    assert app_client.get("/api/v1/remote").json()["allowed"] == [OWNER, "phone@x.io"]
    bad = app_client.put("/api/v1/remote/settings", json={"allowed": ["a b"], "autostart": False})
    assert bad.status_code == 422


def test_autostart_serves_again_when_the_app_starts(config: Settings, fake: FakeTailscale) -> None:
    on = config.model_copy(update={"remote_autostart": True, "remote_allowed": [OWNER]})
    with TestClient(create_app(on)):
        pass  # shutdown waits for the start-up serve call
    assert any(c[:4] == ["serve", "--bg", "--set-path", "/argos"] for c in fake.calls)


# --- the gate ------------------------------------------------------------------------------


def remote_headers(login: str | None) -> dict[str, str]:
    """What Tailscale serve adds (it overwrites these if the caller sent them)."""
    headers = {"Tailscale-Headers-Info": "https://tailscale.com/s/serve-headers"}
    if login:
        headers["Tailscale-User-Login"] = login
    return headers


@pytest.fixture
def gated(config: Settings, fake: FakeTailscale) -> Iterator[TestClient]:
    allowed = config.model_copy(update={"remote_allowed": [OWNER]})
    with TestClient(create_app(allowed)) as c:
        yield c


def test_allowed_account_gets_in_under_the_path(gated: TestClient) -> None:
    ok = gated.get("/argos/api/v1/channels", headers=remote_headers("Owner@example.com"))
    assert ok.status_code == 200 and ok.json()["channels"]


def test_other_accounts_and_unknown_callers_are_refused(gated: TestClient) -> None:
    other = gated.get("/argos/api/v1/channels", headers=remote_headers("friend@example.com"))
    assert other.status_code == 403 and other.json()["error"]["code"] == "forbidden"
    tagged = gated.get("/argos/api/v1/channels", headers=remote_headers(None))  # no person
    assert tagged.status_code == 403
    no_headers = gated.get("/argos/api/v1/channels")  # the path without serve
    assert no_headers.status_code == 403
    # Serve pointed at the root (by hand) still needs an allowed account.
    assert gated.get("/api/v1/channels", headers=remote_headers("x@y.z")).status_code == 403


def test_this_mac_is_unchanged(gated: TestClient) -> None:
    assert gated.get("/api/v1/channels").status_code == 200


def test_nobody_is_allowed_until_turned_on(app_client: TestClient) -> None:
    refused = app_client.get("/argos/api/v1/channels", headers=remote_headers(OWNER))
    assert refused.status_code == 403


def test_websocket_through_serve(gated: TestClient) -> None:
    with gated.websocket_connect("/argos/ws", headers=remote_headers(OWNER)) as ws:
        ws.send_text("ping")  # connected; the hub ignores text
    with (
        pytest.raises(WebSocketDisconnect),
        gated.websocket_connect("/argos/ws", headers=remote_headers("friend@example.com")) as ws,
    ):
        ws.receive_text()


def test_built_page_under_the_path(config: Settings, fake: FakeTailscale, tmp_path: Path) -> None:
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text(
        '<!doctype html><html><head>\n<link rel="icon" href="/favicon.svg" />'
        '<script type="module" crossorigin src="/assets/index-1a.js"></script>'
        '<link rel="preconnect" href="https://fonts.googleapis.com" /></head></html>'
    )
    (web / "assets" / "index-1a.js").write_text("console.log(1)")
    on = config.model_copy(update={"static_dir": web, "remote_allowed": [OWNER]})
    with TestClient(create_app(on)) as c:
        page = c.get("/argos/c/abc/kanban", headers=remote_headers(OWNER))
        assert page.status_code == 200
        assert 'src="/argos/assets/index-1a.js"' in page.text
        assert 'href="/argos/favicon.svg"' in page.text
        assert 'href="https://fonts.googleapis.com"' in page.text
        assert '<meta name="argos-base" content="/argos" />' in page.text
        asset = c.get("/argos/assets/index-1a.js", headers=remote_headers(OWNER))
        assert asset.text == "console.log(1)"
        local = c.get("/c/abc")  # the desktop window: the page as built
        assert "argos-base" not in local.text and 'src="/assets/index-1a.js"' in local.text


def test_remote_path_setting(config: Settings) -> None:
    custom = copy.copy(config)
    custom.remote_path = "/phone/"
    assert remote.remote_path(custom) == "/phone"
    assert remote.target_url(custom) == "http://127.0.0.1:8000/phone"
    custom.remote_target = "http://127.0.0.1:5273/"
    assert remote.target_url(custom) == "http://127.0.0.1:5273/phone"
