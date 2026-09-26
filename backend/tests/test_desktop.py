"""Phase 12: the desktop app's server — data folder, serving the built frontend, the
Hermes key read from Hermes' own .env, the user's PATH and opening the app at login."""

import plistlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from argos import desktop, ops
from argos.config import Settings
from argos.main import create_app


def bare(tmp_path: Path, **extra: object) -> Settings:
    return Settings(_env_file=None, hermes_home=tmp_path / "hermes", **extra)  # pyright: ignore[reportCallIssue, reportArgumentType]


def test_relative_paths_live_in_the_data_folder(tmp_path: Path) -> None:
    config = bare(tmp_path, data_dir=tmp_path / "Argos", backup_dir=tmp_path / "elsewhere")
    assert config.db_path == tmp_path / "Argos" / "argos.db"
    assert config.codex_home == tmp_path / "Argos" / "codex-home"
    assert config.job_roots == [tmp_path / "Argos" / "jobs"]
    assert config.backup_dir == tmp_path / "elsewhere"  # absolute paths stay


def test_hermes_key_comes_from_hermes_env(tmp_path: Path) -> None:
    assert bare(tmp_path).hermes_api_key is None
    (tmp_path / "hermes").mkdir()
    (tmp_path / "hermes" / ".env").write_text('API_SERVER_ENABLED=true\nAPI_SERVER_KEY="k-123"\n')
    key = bare(tmp_path).hermes_api_key
    assert key is not None and key.get_secret_value() == "k-123"
    set_here = bare(tmp_path, hermes_api_key="mine").hermes_api_key
    assert set_here is not None and set_here.get_secret_value() == "mine"


def test_serves_the_built_frontend(settings: Settings, tmp_path: Path) -> None:
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<div id=root></div>")
    (web / "assets" / "app-1a2b.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("no")
    config = settings.model_copy(update={"static_dir": web})
    with TestClient(create_app(config)) as client:
        page = client.get("/c/some-channel/kanban")
        assert page.status_code == 200 and "root" in page.text
        assert page.headers["cache-control"] == "no-cache"
        asset = client.get("/assets/app-1a2b.js")
        assert "immutable" in asset.headers["cache-control"]
        assert "root" in client.get("/../secret.txt").text  # never outside the folder
        assert client.get("/api/v1/nope").status_code == 404
        assert client.get("/api/v1/channels").status_code == 200


def test_no_frontend_without_a_build(client: TestClient) -> None:
    assert client.get("/settings").status_code == 404


def test_path_from_the_login_shell(tmp_path: Path) -> None:
    shell = tmp_path / "fake-shell"
    shell.write_text(
        '#!/bin/sh\necho "welcome banner"\nprintf "__ARGOS_PATH__/opt/tools/bin:/usr/bin"\n'
    )
    shell.chmod(0o755)
    found = desktop.login_path(str(shell))
    assert found == "/opt/tools/bin:/usr/bin"
    merged = desktop.merged_path(found, "/usr/bin:/bin")
    assert merged.split(":")[:3] == ["/opt/tools/bin", "/usr/bin", "/bin"]
    assert "/opt/homebrew/bin" in merged
    assert desktop.login_path(str(tmp_path / "missing")) is None


def test_desktop_app_opens_at_login(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, ...]] = []

    def launchctl(*args: str) -> None:
        calls.append(args)

    def plist_path(label: str = ops.LABEL) -> Path:
        return tmp_path / f"{label}.plist"

    monkeypatch.setattr(ops, "_launchctl", launchctl)
    monkeypatch.setattr(ops, "plist_path", plist_path)
    monkeypatch.setattr(sys, "platform", "darwin")
    app = Path("/Applications/Argos.app")
    config = settings.model_copy(update={"desktop_app": app})

    state = ops.install_service(config, start_now=True)
    assert state.desktop and state.installed and state.running
    spec = plistlib.loads((tmp_path / f"{ops.DESKTOP_LABEL}.plist").read_bytes())
    assert spec["ProgramArguments"] == ["/usr/bin/open", "-g", "-a", str(app)]
    assert "KeepAlive" not in spec
    assert calls == []  # nothing started or stopped: the app is already running

    assert not ops.uninstall_service(config).installed
    assert calls == []


def test_server_exits_when_the_app_is_gone() -> None:
    import subprocess
    import time

    gone = subprocess.Popen(["/usr/bin/true"])
    gone.wait()
    script = (
        "import os, time; from argos import desktop; "
        f"os.environ['ARGOS_PARENT_PID'] = '{gone.pid}'; "
        "desktop.exit_with_parent(); time.sleep(20)"
    )
    started = time.monotonic()
    done = subprocess.run([sys.executable, "-c", script], timeout=15)
    assert done.returncode == 0 and time.monotonic() - started < 10


def test_update_with_new_migrations_backs_up_first(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alembic.config import Config

    from alembic import command
    from argos import config as config_module

    config = settings.model_copy(update={"db_path": tmp_path / "app.db"})
    monkeypatch.setattr(config_module, "settings", config)  # what alembic/env.py reads
    older = Config()
    older.set_main_option("script_location", str(desktop.BUNDLE / "alembic"))
    command.upgrade(older, "head")
    command.downgrade(older, "-1")  # as if the last release had one migration less

    desktop.migrate(config)
    backups = ops.list_backups(config.backup_dir)
    assert len(backups) == 1
    desktop.migrate(config)  # nothing new: no second backup
    assert len(ops.list_backups(config.backup_dir)) == 1
