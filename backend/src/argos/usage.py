"""How much of Claude's and Codex's plan limits is used (PLAN Phase 9).

Neither is an official contract, so each provider lives here alone and fails soft:
a missing or broken source makes that provider "unavailable", never an app error.

- Claude: the usage endpoint Claude Code's own /usage view reads
  (`GET api.anthropic.com/api/oauth/usage`), with the login Claude Code saved (macOS
  Keychain "Claude Code-credentials", else `~/.claude/.credentials.json`). The same
  approach as the Orca app. The token is read when needed, sent only to Anthropic,
  never stored or logged, and never refreshed here (that would sign Claude Code out).
- Codex: the app-server's `account/rateLimits/read` request. Windows are told apart by
  `windowDurationMins` (300 → 5h, 10080 → 7d), not by primary/secondary."""

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, cast

import httpx2

from argos.agents import AgentUnavailable, prepare_codex_home
from argos.config import Settings
from argos.hub import hub

log = logging.getLogger(__name__)

WINDOW_NAMES = {300: "5h", 10080: "7d"}
CLAUDE_WINDOWS = {"five_hour": "5h", "seven_day": "7d"}


@dataclass
class Window:
    name: str  # "5h", "7d", or "<minutes>m"
    used_percent: float
    resets_at: datetime | None


@dataclass
class ProviderUsage:
    provider: str  # "claude" | "codex"
    state: str = "unavailable"  # "ok" | "unavailable" | "error"
    windows: list[Window] = field(default_factory=list[Window])
    observed_at: datetime | None = None
    message: str | None = None  # why it is unavailable, for the settings screen
    plan: str | None = None

    def window(self, name: str, now: datetime) -> Window | None:
        """A window that is still current (a passed reset time means the value is stale)."""
        for w in self.windows:
            if w.name == name and (w.resets_at is None or w.resets_at > now):
                return w
        return None


class UsageProvider(Protocol):
    name: str

    async def read(self) -> ProviderUsage: ...


def _epoch(value: object) -> datetime | None:
    if isinstance(value, int | float) and value > 0:
        return datetime.fromtimestamp(value, UTC)
    return None


def _dict(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


# --- Claude ---------------------------------------------------------------------------------

CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
KEYCHAIN_SERVICE = "Claude Code-credentials"
SIGN_IN = "Claude Code에서 로그인(또는 한 번 실행)하면 다시 보여요"


def _when(value: object) -> datetime | None:
    """Reset times come as epoch seconds/milliseconds or ISO text."""
    if isinstance(value, int | float):
        return _epoch(value / 1000 if value > 1e10 else value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def parse_claude(data: object, now: datetime) -> ProviderUsage:
    body = _dict(data)
    usage = ProviderUsage("claude", observed_at=now)
    for key, name in CLAUDE_WINDOWS.items():
        window = _dict(body.get(key))
        percent = window.get("utilization", window.get("used_percentage"))
        if isinstance(percent, int | float):
            used = min(100.0, max(0.0, float(percent)))
            usage.windows.append(Window(name, used, _when(window.get("resets_at"))))
    usage.state = "ok" if usage.windows else "unavailable"
    if not usage.windows:
        usage.message = "Claude가 사용량을 알려주지 않았어요 (Pro·Max 요금제 로그인에서만 보여요)"
    return usage


@dataclass
class ClaudeLogin:
    token: str
    expires_at: datetime | None


def parse_credentials(text: str) -> ClaudeLogin | None:
    try:
        oauth = _dict(_dict(json.loads(text)).get("claudeAiOauth"))
    except ValueError:
        return None
    token = oauth.get("accessToken")
    if not isinstance(token, str) or not token.strip():
        return None
    return ClaudeLogin(token.strip(), _when(oauth.get("expiresAt")))


def read_claude_login(claude_dir: Path) -> ClaudeLogin | None:
    """Claude Code's saved login: the macOS Keychain first (where it keeps it on a
    Mac), then its credentials file (Linux, Windows, older setups)."""
    if sys.platform == "darwin" and (security := shutil.which("security")):
        try:
            found = subprocess.run(
                [security, "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                capture_output=True,
                text=True,
                timeout=30,  # the first time, macOS may ask the user to allow access
            )
        except (OSError, subprocess.TimeoutExpired):
            found = None
        if found is not None and found.returncode == 0:
            if (login := parse_credentials(found.stdout)) is not None:
                return login
    try:
        return parse_credentials((claude_dir / ".credentials.json").read_text())
    except OSError:
        return None


LoginReader = Callable[[], "ClaudeLogin | None"]


def _claude_down(message: str, error: bool = False) -> ProviderUsage:
    return ProviderUsage("claude", state="error" if error else "unavailable", message=message)


class ClaudeOAuthUsage:
    name = "claude"

    def __init__(
        self,
        login: LoginReader,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._login = login
        self._transport = transport

    async def read(self) -> ProviderUsage:
        login = await asyncio.to_thread(self._login)
        now = datetime.now(UTC)
        if login is None:
            return _claude_down(f"Claude Code 로그인을 찾지 못했어요. {SIGN_IN}")
        if login.expires_at is not None and login.expires_at <= now:
            return _claude_down(f"Claude Code 로그인이 만료됐어요. {SIGN_IN}")
        headers = {
            "Authorization": f"Bearer {login.token}",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": "Argos",
        }
        try:
            async with httpx2.AsyncClient(timeout=10, transport=self._transport) as http:
                response = await http.get(CLAUDE_USAGE_URL, headers=headers)
        except httpx2.HTTPError as exc:  # the message never carries the token
            return _claude_down(f"사용량을 가져오지 못했어요 ({type(exc).__name__})", error=True)
        if response.status_code in (401, 403):
            return _claude_down(f"Claude Code 로그인이 만료됐어요. {SIGN_IN}")
        if response.status_code == 429:
            return _claude_down("잠시 뒤 다시 읽을게요 (요청이 많아요)", error=True)
        if response.status_code >= 400:
            return _claude_down(f"사용량을 가져오지 못했어요 ({response.status_code})", error=True)
        try:
            return parse_claude(response.json(), now)
        except ValueError:
            return _claude_down("사용량 응답을 읽지 못했어요", error=True)


# --- Codex ----------------------------------------------------------------------------------


def parse_codex(result: object, now: datetime) -> ProviderUsage:
    body = _dict(result)
    limits = _dict(body.get("rateLimits"))
    usage = ProviderUsage("codex", observed_at=now)
    plan = limits.get("planType")
    usage.plan = plan if isinstance(plan, str) else None
    for key in ("primary", "secondary"):
        window = _dict(limits.get(key))
        percent = window.get("usedPercent")
        minutes = window.get("windowDurationMins")
        if not isinstance(percent, int | float):
            continue
        name = WINDOW_NAMES.get(minutes, f"{minutes}m") if isinstance(minutes, int) else key
        usage.windows.append(Window(name, float(percent), _epoch(window.get("resetsAt"))))
    usage.state = "ok" if usage.windows else "unavailable"
    if not usage.windows:
        usage.message = "Codex가 사용량을 알려주지 않았어요"
    return usage


class CodexAppServer:
    name = "codex"

    def __init__(self, binary: str, home: Path) -> None:
        self.binary = binary
        self.home = home

    async def read(self) -> ProviderUsage:
        path = shutil.which(self.binary)
        if path is None:
            return ProviderUsage("codex", message="Codex CLI가 설치되어 있지 않아요")
        try:
            process = await asyncio.create_subprocess_exec(
                path,
                "app-server",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env={**os.environ, "CODEX_HOME": str(self.home)},
            )
        except OSError:
            return ProviderUsage("codex", state="error", message="Codex를 실행하지 못했어요")
        try:
            async with asyncio.timeout(20):
                return await self._ask(process)
        except (TimeoutError, ValueError, ConnectionError):
            return ProviderUsage("codex", state="error", message="Codex 사용량을 읽지 못했어요")
        finally:
            if process.stdin is not None:
                process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 2)
            except TimeoutError:
                process.kill()
                await process.wait()

    async def _ask(self, process: asyncio.subprocess.Process) -> ProviderUsage:
        assert process.stdin is not None and process.stdout is not None
        for message in (
            {
                "id": 1,
                "method": "initialize",
                "params": {"clientInfo": {"name": "argos", "version": "1"}},
            },
            {"method": "initialized"},
            {"id": 2, "method": "account/rateLimits/read"},
        ):
            process.stdin.write((json.dumps(message) + "\n").encode())
        await process.stdin.drain()
        while line := await process.stdout.readline():
            reply = _dict(json.loads(line))
            if reply.get("id") != 2:
                continue
            if "error" in reply:  # e.g. signed in with an API key: no plan limits
                return ProviderUsage("codex", message="이 Codex 계정에는 요금제 사용량이 없어요")
            return parse_codex(reply.get("result"), datetime.now(UTC))
        raise ConnectionError("app-server closed")


# --- monitor ----------------------------------------------------------------------------------


class UsageMonitor:
    """Latest usage per provider: Claude on file changes, Codex on a timer and after
    agent runs. Changes go out as `usage.updated`."""

    def __init__(self, settings: Callable[[], Settings]) -> None:
        self._settings = settings
        self.current: dict[str, ProviderUsage] = {
            "claude": ProviderUsage("claude"),
            "codex": ProviderUsage("codex"),
        }
        self._tasks: set[asyncio.Task[Any]] = set()
        self._codex_soon: asyncio.TimerHandle | None = None
        self._stop = asyncio.Event()

    def providers(self) -> dict[str, UsageProvider]:
        config = self._settings()
        return {
            "claude": ClaudeOAuthUsage(lambda: read_claude_login(config.claude_dir.expanduser())),
            "codex": CodexAppServer(
                config.codex_bin, prepare_codex_home(config.codex_home, config.codex_auth)
            ),
        }

    async def refresh(self, name: str) -> ProviderUsage:
        try:
            usage = await self.providers()[name].read()
        except AgentUnavailable as exc:  # e.g. not signed in to Codex
            usage = ProviderUsage(name, message=str(exc))
        except Exception:
            log.exception("usage read failed: %s", name)
            usage = ProviderUsage(name, state="error", message="사용량을 읽지 못했어요")
        if usage != self.current.get(name):
            self.current[name] = usage
            await hub.publish("usage.updated", usage_json(usage))
        return usage

    def refresh_soon(self, delay: float = 10.0) -> None:
        """After an agent run: the limits moved; read both once things settle."""
        if self._codex_soon is not None:
            return

        def fire() -> None:
            self._codex_soon = None
            self._spawn(self.refresh("claude"))
            self._spawn(self.refresh("codex"))

        self._codex_soon = asyncio.get_running_loop().call_later(delay, fire)

    def _spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def start(self) -> None:
        minutes = self._settings().usage_poll_minutes
        if minutes > 0:
            self._spawn(self._poll(minutes))

    async def _poll(self, minutes: int) -> None:
        while not self._stop.is_set():
            await asyncio.gather(self.refresh("claude"), self.refresh("codex"))
            try:
                await asyncio.wait_for(self._stop.wait(), minutes * 60)
            except TimeoutError:
                pass

    async def stop(self) -> None:
        self._stop.set()
        if self._codex_soon is not None:
            self._codex_soon.cancel()
        for task in list(self._tasks):
            task.cancel()


def usage_json(usage: ProviderUsage) -> dict[str, Any]:
    data = asdict(usage)
    for window in data["windows"]:
        window["resets_at"] = window["resets_at"].isoformat() if window["resets_at"] else None
    data["observed_at"] = usage.observed_at.isoformat() if usage.observed_at else None
    return data


def busy_note(usage: ProviderUsage, agent: str, others: list[str], now: datetime) -> str | None:
    """A line for the thread when the agent's 5-hour window is nearly used up (≥ 90%)."""
    window = usage.window("5h", now)
    if window is None or window.used_percent < 90:
        return None
    left = ""
    if window.resets_at is not None:
        minutes = max(1, int((window.resets_at - now) / timedelta(minutes=1)))
        left = (
            f", {minutes // 60}시간 {minutes % 60}분 뒤 초기화"
            if minutes >= 60
            else f", {minutes}분 뒤 초기화"
        )
    alternatives = " 또는 ".join(f"@{o}" for o in others)
    suggestion = f" {alternatives}에게 보내려면 다시 불러 주세요." if alternatives else ""
    return f"{agent}의 5시간 사용량이 {window.used_percent:.0f}%예요{left}.{suggestion}"
