"""Keeping Argos running (PLAN Phase 11): daily database backups and starting at login
through launchd (macOS). The desktop app (Phase 12) will take over starting."""

import asyncio
import logging
import os
import plistlib
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from argos.config import Settings

log = logging.getLogger(__name__)

# --- backups ----------------------------------------------------------------------------------

BACKUP_EVERY = timedelta(days=1)


def list_backups(directory: Path) -> list[Path]:
    return sorted(directory.glob("argos-*.db")) if directory.is_dir() else []


def backup_db(db_path: Path, directory: Path, keep: int, now: datetime) -> Path:
    """A consistent copy through SQLite's online backup (safe while the app writes),
    then only the newest `keep` copies stay."""
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"argos-{now:%Y%m%d-%H%M%S}.db"
    source = sqlite3.connect(db_path)
    try:
        copy = sqlite3.connect(target)
        try:
            source.backup(copy)
        finally:
            copy.close()
    finally:
        source.close()
    for old in list_backups(directory)[: -max(1, keep)]:
        old.unlink(missing_ok=True)
    return target


class Backups:
    """Backs up at start when the last copy is older than a day, then daily."""

    def __init__(self, settings: Callable[[], Settings]) -> None:
        self._settings = settings
        self._loop: asyncio.Task[None] | None = None
        self.last_error: str | None = None

    def last(self) -> Path | None:
        found = list_backups(self._settings().backup_dir)
        return found[-1] if found else None

    async def run(self) -> Path:
        config = self._settings()
        try:
            path = await asyncio.to_thread(
                backup_db, config.db_path, config.backup_dir, config.backup_keep, datetime.now()
            )
        except (OSError, sqlite3.Error) as exc:
            self.last_error = f"백업하지 못했어요: {exc}"
            raise
        self.last_error = None
        return path

    def _due(self) -> bool:
        last = self.last()
        if last is None:
            return True
        made = datetime.fromtimestamp(last.stat().st_mtime, UTC)
        return datetime.now(UTC) - made >= BACKUP_EVERY

    def start(self) -> None:
        async def loop() -> None:
            while True:
                if self._due():
                    try:
                        await self.run()
                    except (OSError, sqlite3.Error):
                        log.exception("backup failed")
                await asyncio.sleep(3600)

        self._loop = asyncio.create_task(loop())

    async def stop(self) -> None:
        if self._loop is not None:
            self._loop.cancel()
            try:
                await self._loop
            except asyncio.CancelledError:
                pass


# --- start at login (launchd) -----------------------------------------------------------------

LABEL = "app.argos.server"
REPO = Path(__file__).resolve().parents[3]  # the checkout: backend/src/argos/ops.py


@dataclass
class ServiceState:
    supported: bool
    installed: bool  # the LaunchAgent file is there: starts at the next login
    running: bool  # launchd has it loaded now
    plist: str
    log: str
    problem: str | None = None


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def service_plist(repo: Path, log_file: Path) -> bytes:
    """Runs `make dev` in the checkout (backend + frontend, like in a terminal) and
    restarts it if it stops. launchd starts with a bare PATH, so the folders of the
    tools it needs are spelled out."""
    tools = [shutil.which(t) for t in ("make", "uv", "bun", "node")]
    folders = [str(Path(t).parent) for t in tools if t]
    system = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    path = ":".join(dict.fromkeys([*folders, *system]))
    return plistlib.dumps(
        {
            "Label": LABEL,
            "ProgramArguments": [shutil.which("make") or "/usr/bin/make", "-C", str(repo), "dev"],
            "WorkingDirectory": str(repo),
            "EnvironmentVariables": {"PATH": path},
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "ThrottleInterval": 30,
            "StandardOutPath": str(log_file),
            "StandardErrorPath": str(log_file),
        }
    )


def _launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=30)


def _domain() -> str:
    return f"gui/{os.getuid()}"


def service_state(settings: Settings) -> ServiceState:
    log_file = (settings.db_path.parent / "logs" / "service.log").resolve()
    state = ServiceState(
        supported=sys.platform == "darwin",
        installed=plist_path().exists(),
        running=False,
        plist=str(plist_path()),
        log=str(log_file),
    )
    if not state.supported:
        state.problem = "자동 실행 등록은 macOS에서만 돼요"
        return state
    state.running = _launchctl("print", f"{_domain()}/{LABEL}").returncode == 0
    return state


def install_service(settings: Settings, start_now: bool = False) -> ServiceState:
    """Writes the LaunchAgent. It starts at the next login; `start_now` also starts it
    now (only when nothing else is running Argos on the same ports)."""
    if sys.platform != "darwin":
        return service_state(settings)
    log_file = (settings.db_path.parent / "logs" / "service.log").resolve()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    target = plist_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(service_plist(REPO, log_file))
    if start_now:
        _launchctl("bootout", f"{_domain()}/{LABEL}")
        done = _launchctl("bootstrap", _domain(), str(target))
        if done.returncode != 0:
            state = service_state(settings)
            state.problem = f"시작하지 못했어요: {done.stderr.strip()[:200]}"
            return state
    return service_state(settings)


def uninstall_service(settings: Settings) -> ServiceState:
    """Stops it if launchd has it loaded and removes the LaunchAgent."""
    if sys.platform == "darwin":
        _launchctl("bootout", f"{_domain()}/{LABEL}")
        plist_path().unlink(missing_ok=True)
    return service_state(settings)
