"""The server inside the desktop app (PLAN Phase 12): a PyInstaller sidecar the Tauri
shell starts. It finds the user's tools, applies migrations, then serves the API and the
built frontend on one port. Run from a checkout with `uv run python -m argos.desktop`."""

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argos.config import Settings

# Bundled files (migrations, seed, built frontend): PyInstaller unpacks them to
# sys._MEIPASS; in a checkout they sit in backend/ (and frontend/dist).
BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
DEFAULT_DATA = Path.home() / "Library" / "Application Support" / "Argos"


def login_path(shell: str | None = None) -> str | None:
    """Apps opened from Finder get a bare PATH (/usr/bin:/bin…), so claude, codex, hermes,
    ollama and tailscale would not be found. Ask the user's login shell once."""
    shell = shell or os.environ.get("SHELL") or "/bin/zsh"
    try:
        done = subprocess.run(
            [shell, "-ilc", 'printf "__ARGOS_PATH__%s" "$PATH"'],
            capture_output=True,
            text=True,
            timeout=15,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    _, sep, path = done.stdout.rpartition("__ARGOS_PATH__")
    return (path.strip() or None) if sep else None


def merged_path(found: str | None, current: str) -> str:
    extra = ["/opt/homebrew/bin", "/usr/local/bin", str(Path.home() / ".local" / "bin")]
    parts = [*(found or "").split(":"), *current.split(":"), *extra]
    return ":".join(dict.fromkeys(p for p in parts if p))


def prepare_environment() -> Path:
    """Environment for Settings, set before argos.config is imported."""
    os.environ["PATH"] = merged_path(login_path(), os.environ.get("PATH", ""))
    data = Path(os.environ.setdefault("ARGOS_DATA_DIR", str(DEFAULT_DATA))).expanduser()
    data.mkdir(parents=True, exist_ok=True)
    os.chdir(data)  # an optional .env next to the data is read from here
    os.environ.setdefault("ARGOS_SEED_PATH", str(BUNDLE / "seed.desktop.toml"))
    web = BUNDLE / "web" if (BUNDLE / "web").is_dir() else BUNDLE.parent / "frontend" / "dist"
    if (web / "index.html").is_file():
        os.environ.setdefault("ARGOS_STATIC_DIR", str(web))
    return data


def migrate(settings: "Settings") -> None:
    """Brings the database up to this version. When an update brings new migrations, the
    database is backed up first, so a bad migration can be undone by hand."""
    import sqlite3
    from datetime import datetime

    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine

    from alembic import command
    from argos import ops

    config = Config()
    config.set_main_option("script_location", str(BUNDLE / "alembic"))
    if settings.db_path.exists():
        engine = create_engine(f"sqlite:///{settings.db_path}")
        with engine.connect() as conn:
            current = MigrationContext.configure(conn).get_current_revision()
        engine.dispose()
        head = ScriptDirectory.from_config(config).get_current_head()
        if current is not None and current != head:
            try:
                ops.backup_db(
                    settings.db_path, settings.backup_dir, settings.backup_keep, datetime.now()
                )
            except (OSError, sqlite3.Error):
                logging.exception("backup before migrating failed")
    command.upgrade(config, "head")


def exit_with_parent() -> None:
    """If the app is killed without stopping us, don't keep holding the port. The app
    passes its own pid (with PyInstaller's one-file loader in between, our parent is the
    loader, not the app)."""
    try:
        app = int(os.environ.get("ARGOS_PARENT_PID", ""))
    except ValueError:
        return

    def watch() -> None:
        while True:
            time.sleep(2)
            try:
                os.kill(app, 0)
            except ProcessLookupError:
                os._exit(0)
            except PermissionError:
                pass  # alive, owned by someone else

    threading.Thread(target=watch, daemon=True).start()


def main() -> None:
    data = prepare_environment()
    logs = data / "logs"
    logs.mkdir(exist_ok=True)
    logging.basicConfig(
        filename=logs / "server.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    exit_with_parent()

    import uvicorn

    from argos.config import settings
    from argos.main import create_app

    migrate(settings)

    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_config=None)


if __name__ == "__main__":
    main()
