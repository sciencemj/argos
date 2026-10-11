"""Opening Argos on the user's phone through Tailscale serve (PLAN Phase 14).

`tailscale serve --bg --set-path /argos http://127.0.0.1:<port>/argos` puts the app on the
tailnet under a path (the root stays free for other services). Serve forwards the path as
is and adds the caller's Tailscale identity (`Tailscale-User-Login`, overwriting whatever
the caller sent). `RemoteGate` lets such requests through only for allowed accounts and
strips the path prefix so the rest of the app works unchanged. Requests made on this Mac
directly (no serve headers, no prefix) pass as before.
"""

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send

from argos.config import Settings

APP_CLI = Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale")
# `serve` waits (polling) when HTTPS is off for the tailnet; give up and show its link.
SERVE_TIMEOUT = 20.0
ENABLE_LINK = re.compile(r"https://login\.tailscale\.com/\S+")


def _run(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def find_cli(settings: Settings) -> str | None:
    """The configured CLI, else `tailscale` on PATH, else the one inside Tailscale.app."""
    if settings.tailscale_bin:
        return settings.tailscale_bin
    return shutil.which("tailscale") or (str(APP_CLI) if APP_CLI.exists() else None)


def remote_path(settings: Settings) -> str:
    """ "/argos" for "argos", "/argos/" or "/argos"; "" would mean the root (not allowed)."""
    return "/" + settings.remote_path.strip("/")


def target_url(settings: Settings) -> str:
    """Where serve forwards to: this server (or `remote_target`, e.g. Vite in development),
    with the path kept so the app knows it was opened remotely."""
    base = settings.remote_target or f"http://127.0.0.1:{settings.port}"
    return base.rstrip("/") + remote_path(settings)


@dataclass
class RemoteState:
    installed: bool  # a Tailscale CLI was found
    running: bool = False  # logged in and connected
    login: str | None = None  # this Mac's Tailscale account
    host: str | None = None  # e.g. "macbook.tailnet.ts.net"
    enabled: bool = False  # serve has our path
    proxy: str | None = None  # what serve forwards our path to now
    target: str = ""  # what it should forward to
    url: str | None = None  # the address to open on the phone
    allowed: list[str] = field(default_factory=list[str])
    autostart: bool = False
    problem: str | None = None
    enable_link: str | None = None  # Tailscale's page for turning HTTPS on


class Tailscale:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.cli = find_cli(settings)

    def _call(self, *args: str, timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
        assert self.cli is not None
        return _run([self.cli, *args], timeout)

    def _json(self, *args: str) -> dict[str, Any] | None:
        try:
            done = self._call(*args, "--json")
        except (OSError, subprocess.TimeoutExpired):
            return None
        if done.returncode != 0:
            return None
        try:
            data = json.loads(done.stdout or "{}")
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None  # pyright: ignore[reportUnknownVariableType]

    def state(self) -> RemoteState:
        settings = self.settings
        path = remote_path(settings)
        state = RemoteState(
            installed=self.cli is not None,
            target=target_url(settings),
            allowed=list(settings.remote_allowed),
            autostart=settings.remote_autostart,
        )
        if self.cli is None:
            state.problem = "이 Mac에서 Tailscale을 찾지 못했어요"
            return state
        status = self._json("status")
        if status is None:
            state.problem = (
                "Tailscale 상태를 읽지 못했어요. Tailscale 앱이 켜져 있는지 확인해 주세요"
            )
            return state
        state.running = status.get("BackendState") == "Running"
        me: dict[str, Any] = status.get("Self") or {}
        users: dict[str, Any] = status.get("User") or {}
        user: dict[str, Any] = users.get(str(me.get("UserID")), {})
        state.login = user.get("LoginName") or None
        state.host = (me.get("DNSName") or "").rstrip(".") or None
        if not state.running:
            state.problem = "Tailscale에 로그인하고 연결해 주세요"
            return state
        serve = self._json("serve", "status") or {}
        web: dict[str, Any] = serve.get("Web") or {}
        for hostport, conf in web.items():
            handler = _dict(_dict(conf).get("Handlers")).get(path)
            if handler is not None and hostport.endswith(":443"):
                state.enabled = True
                state.proxy = _dict(handler).get("Proxy")
        if state.host:
            state.url = f"https://{state.host}{path}/"
        return state

    def serve_on(self) -> RemoteState:
        """Adds our path (replacing what it pointed to before); other paths stay."""
        path = remote_path(self.settings)
        try:
            done = self._call(
                "serve", "--bg", "--set-path", path, target_url(self.settings),
                timeout=SERVE_TIMEOUT,
            )  # fmt: skip
            output, failed = (done.stdout or "") + (done.stderr or ""), done.returncode != 0
        except subprocess.TimeoutExpired as waiting:  # HTTPS not enabled: serve waits for it
            output, failed = _text(waiting.stdout) + _text(waiting.stderr), True
        except OSError as exc:
            output, failed = str(exc), True
        state = self.state()
        if failed:
            link = ENABLE_LINK.search(output)
            state.enable_link = link.group(0) if link else None
            state.problem = (
                "이 tailnet에서 HTTPS(Serve)를 먼저 켜야 해요. 아래 링크에서 켠 뒤 다시 눌러 주세요"
                if link
                else f"Tailscale serve를 켜지 못했어요: {output.strip()[:200]}"
            )
        return state

    def serve_off(self) -> RemoteState:
        """Removes only our path."""
        path = remote_path(self.settings)
        try:
            self._call("serve", "--bg", "--set-path", path, "off")
        except (OSError, subprocess.TimeoutExpired):
            pass
        return self.state()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}  # pyright: ignore[reportUnknownVariableType]


def _text(value: bytes | str | None) -> str:
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value or ""


# --- the gate ------------------------------------------------------------------------------


def _forbidden_body(reason: str) -> bytes:
    return json.dumps({"error": {"code": "forbidden", "message": reason}}).encode()


class RemoteGate:
    """Checks requests that came through Tailscale serve and strips the remote prefix.

    Through serve (identity headers present, or under the remote path): the Tailscale
    account must be in `remote_allowed`. Otherwise (this Mac): unchanged. The settings are
    read from `app.state.settings` per request, so changes apply at once.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        settings: Settings | None = getattr(scope["app"].state, "settings", None)
        prefix = remote_path(settings) if settings else None
        path: str = scope["path"]
        under = prefix is not None and (path == prefix or path.startswith(prefix + "/"))
        headers = dict(scope["headers"])
        login_raw = headers.get(b"tailscale-user-login")
        via_serve = login_raw is not None or b"tailscale-headers-info" in headers
        if via_serve or under:
            login = login_raw.decode(errors="replace").strip().lower() if login_raw else None
            allowed = {a.strip().lower() for a in (settings.remote_allowed if settings else [])}
            if login is None or login not in allowed:
                reason = (
                    "이 기기의 Tailscale 계정은 Argos에 허용되지 않았어요"
                    if login
                    else "Tailscale 계정을 확인할 수 없어요"
                )
                await _deny(scope, receive, send, reason)
                return
        if under and prefix is not None:
            rest = path[len(prefix) :] or "/"
            state = {**scope.get("state", {}), "remote_base": prefix}
            scope = {**scope, "path": rest, "raw_path": rest.encode(), "state": state}
        await self.app(scope, receive, send)


async def _deny(scope: Scope, receive: Receive, send: Send, reason: str) -> None:
    if scope["type"] == "websocket":
        await receive()  # websocket.connect
        await send({"type": "websocket.close", "code": 1008, "reason": reason[:120]})
        return
    body = _forbidden_body(reason)
    await send(
        {
            "type": "http.response.start",
            "status": 403,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def rewrite_index(html: str, base: str) -> str:
    """The built page under the remote path: absolute asset links get the prefix, and the
    app learns its base from a meta tag (frontend/src/base.ts)."""
    html = re.sub(r'(\s(?:src|href))="/(?!/)', rf'\1="{base}/', html)
    return html.replace("<head>", f'<head>\n    <meta name="argos-base" content="{base}" />', 1)
