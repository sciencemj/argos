"""Phase 12: first-run setup — tool detection, connecting the agent CLIs to Argos' MCP
server with the argos skill, and removing both, with fake CLIs that record their calls."""

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from argos import onboarding
from argos.config import Settings
from argos.main import create_app

# A fake CLI: logs its arguments and keeps its MCP entry / Hermes skill dirs in files,
# answering `mcp get` / `config get` the way the real ones print them.
FAKE = """#!/bin/sh
echo "$0 $*" >> "{log}"
name=$(basename "$0")
state="{state}/$name"
case "$*" in
  --version) echo "$name 9.9.9" ;;
  "mcp get argos"*|"config get mcp_servers.argos.url") [ -f "$state" ] && cat "$state" || exit 1 ;;
  "mcp add"*) echo "URL: $(eval echo \\${{$#}})" > "$state" ;;
  "mcp remove"*|"config unset mcp_servers.argos") rm -f "$state" ;;
  "config set mcp_servers.argos.url"*) echo "$4" > "$state" ;;
  "config get skills.external_dirs") cat "$state.dirs" 2>/dev/null || echo "- /old/skills" ;;
  "config set skills.external_dirs"*) echo "$4" | tr -d '[]' | tr ',' '\\n' \
      | sed 's/^ *"//; s/"$//; s/^/- /' > "$state.dirs" ;;
  "config unset skills.external_dirs") echo "" > "$state.dirs" ;;
  "gateway restart") exit 1 ;;
esac
exit 0
"""

# A fake Antigravity CLI (`agy`): `mcp list` prints a table like the real one, `mcp add`
# takes flags before the name, `mcp remove` fails for a server it does not have.
FAKE_AGY = """#!/bin/sh
echo "$0 $*" >> "{log}"
state="{state}/agy"
case "$*" in
  --version) echo "1.5.12" ;;
  "mcp list")
    echo "NAME               TYPE   STATUS   COMMAND/URL"
    echo "notion             http   enabled  https://mcp.notion.com/mcp"
    [ -f "$state" ] && echo "argos              http   enabled  $(cat "$state")" ;;
  "mcp add --type http argos "*) echo "$6" > "$state" ;;
  "mcp remove argos")
    [ -f "$state" ] || {{ echo 'Error: MCP server "argos" not found' >&2; exit 1; }}
    rm -f "$state" ;;
esac
exit 0
"""


@pytest.fixture
def tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir, state = tmp_path / "bin", tmp_path / "state"
    bin_dir.mkdir()
    state.mkdir()
    log = tmp_path / "calls.log"
    for name in ("claude", "codex", "hermes"):  # no ollama
        script = bin_dir / name
        script.write_text(FAKE.format(log=log, state=state))
        script.chmod(0o755)
    agy = bin_dir / "agy"
    agy.write_text(FAKE_AGY.format(log=log, state=state))
    agy.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    return log


@pytest.fixture
def config(settings: Settings, tmp_path: Path) -> Settings:
    return settings.model_copy(
        update={
            "claude_dir": tmp_path / "home" / ".claude",
            "codex_auth": tmp_path / "home" / ".codex" / "auth.json",
            "antigravity_dir": tmp_path / "home" / ".gemini" / "config",
            "antigravity_ide_dir": tmp_path / "home" / ".gemini" / "antigravity",
        }
    )


def calls(log: Path) -> list[str]:
    return [line.split(" ", 1)[1] for line in log.read_text().splitlines()]


def test_setup_connects_mcp_and_skills(config: Settings, tools: Path) -> None:
    with TestClient(create_app(config)) as client:
        setup = client.get("/api/v1/setup").json()
        assert setup["done"] is False and setup["desktop"] is False
        by_name = {t["name"]: t for t in setup["tools"]}
        assert by_name["claude"]["installed"] and by_name["claude"]["version"] == "claude 9.9.9"
        assert not by_name["claude"]["connected"] and not by_name["claude"]["skill"]
        assert not by_name["ollama"]["installed"] and not by_name["ollama"]["connectable"]

        url = f"http://127.0.0.1:{config.port}/mcp?agent="
        claude = client.post("/api/v1/setup/tools/claude/connect").json()
        assert claude["connected"] and claude["skill"]
        skill = config.claude_dir / "skills" / "argos"
        assert "name: argos" in (skill / "SKILL.md").read_text()
        codex = client.post("/api/v1/setup/tools/codex/connect").json()
        assert codex["skill"] and (config.codex_auth.parent / "skills" / "argos").is_dir()
        hermes = client.post("/api/v1/setup/tools/hermes/connect").json()
        assert hermes["connected"] and hermes["skill"]  # though the gateway restart failed
        assert client.post("/api/v1/setup/tools/ollama/connect").status_code == 422

        done = calls(tools)
        assert f"mcp add --transport http --scope user argos {url}claude" in done
        assert "mcp remove argos -s user" in done  # an older entry is replaced
        assert f"mcp add argos --url {url}codex" in done
        assert f"config set mcp_servers.argos.url {url}hermes" in done
        folder = onboarding.hermes_skills_dir(config)
        assert f'config set skills.external_dirs ["/old/skills", "{folder}"]' in done

        # Settings → 에이전트 도구: remove only the skill, then everything.
        only_skill = client.post(
            "/api/v1/setup/tools/claude/disconnect", json={"mcp": False, "skill": True}
        ).json()
        assert only_skill["connected"] and not only_skill["skill"] and not skill.exists()
        gone = client.post("/api/v1/setup/tools/hermes/disconnect", json={}).json()
        assert not gone["connected"] and not gone["skill"]
        assert 'config set skills.external_dirs ["/old/skills"]' in calls(tools)

        assert client.post("/api/v1/setup/done").json()["done"] is True
        assert client.get("/api/v1/setup").json()["done"] is True


def test_someone_elses_skill_is_left_alone(config: Settings, tools: Path) -> None:
    theirs = config.claude_dir / "skills" / "argos"
    theirs.mkdir(parents=True)
    (theirs / "SKILL.md").write_text("mine")
    status = onboarding.tool_status(config, "claude")
    assert status.skill_conflict and not status.skill
    with pytest.raises(onboarding.ConnectError, match="덮어쓰지 않았어요"):
        onboarding.connect(config, "claude")
    onboarding.disconnect(config, "claude", mcp=True, skill=True)
    assert (theirs / "SKILL.md").read_text() == "mine"


def test_missing_tool_cannot_connect(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    with pytest.raises(onboarding.ConnectError):
        onboarding.connect(settings, "claude")


def test_the_app_copies_the_hermes_skill(
    config: Settings, tools: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "bundle"
    for part in ("integrations/hermes/skills/argos", "integrations/skills/argos"):
        (bundle / part).mkdir(parents=True)
        (bundle / part / "SKILL.md").write_text("---\nname: argos\n---\n")
    monkeypatch.setattr(onboarding.sys, "frozen", True, raising=False)
    monkeypatch.setattr(onboarding.sys, "_MEIPASS", str(bundle), raising=False)
    onboarding.connect(config, "hermes")
    target = onboarding.hermes_skills_dir(config)
    assert target == config.data_dir.resolve() / "hermes-skills"
    assert (target / "argos" / "SKILL.md").exists()


def test_refresh_updates_only_what_argos_installed(config: Settings, tools: Path) -> None:
    onboarding.connect(config, "claude")
    skill = config.claude_dir / "skills" / "argos" / "SKILL.md"
    skill.write_text("old version")
    (config.codex_auth.parent / "skills" / "argos").mkdir(parents=True)  # the user's own
    (config.codex_auth.parent / "skills" / "argos" / "SKILL.md").write_text("theirs")
    moved = config.model_copy(update={"port": 8123})  # the MCP address changed

    changed = onboarding.refresh(moved)
    assert "claude skill" in changed and "name: argos" in skill.read_text()
    assert "claude mcp" in changed
    assert onboarding.tool_status(moved, "claude").connected
    # Codex was never connected: its skill folder and MCP stay as they are.
    assert (config.codex_auth.parent / "skills" / "argos" / "SKILL.md").read_text() == "theirs"
    assert not any(c.startswith("codex") or c.startswith("hermes") for c in changed)
    assert onboarding.refresh(moved) == []  # nothing left to do


def test_desktop_seed_has_areas_but_no_channels() -> None:
    from argos import services

    seed = services.load_seed(Path(__file__).parents[1] / "seed.desktop.toml")
    assert [a["name"] for a in seed["area"]] == ["학업", "프로젝트"]
    assert all(not a.get("channel") for a in seed["area"])


def test_uninstall_takes_argos_out_of_every_tool(config: Settings, tools: Path) -> None:
    with TestClient(create_app(config)) as client:
        for name in ("claude", "codex", "hermes", "antigravity"):
            client.post(f"/api/v1/setup/tools/{name}/connect")
        removed = client.post("/api/v1/setup/uninstall").json()["removed"]
        assert removed == [
            "Claude Code: MCP + 스킬 제거",
            "Codex: MCP + 스킬 제거",
            "Hermes Agent: MCP + 스킬 제거",
            "Antigravity: MCP + 스킬 제거",
        ]
        tools_now = {t["name"]: t for t in client.get("/api/v1/setup").json()["tools"]}
        assert not any(t["connected"] or t["skill"] for t in tools_now.values())
        assert not (config.claude_dir / "skills" / "argos").exists()
        assert not (config.antigravity_dir / "skills" / "argos").exists()
        assert client.post("/api/v1/setup/uninstall").json()["removed"] == []  # nothing left


def test_antigravity_connect_status_and_disconnect(config: Settings, tools: Path) -> None:
    url = f"http://127.0.0.1:{config.port}/mcp?agent=antigravity"
    skill = config.antigravity_dir / "skills" / "argos"
    with TestClient(create_app(config)) as client:
        by_name = {t["name"]: t for t in client.get("/api/v1/setup").json()["tools"]}
        agy = by_name["antigravity"]
        assert agy["label"] == "Antigravity" and agy["installed"] and agy["connectable"]
        assert agy["version"] == "1.5.12" and agy["path"].endswith("/agy")
        assert not agy["connected"] and not agy["skill"]
        assert agy["skill_path"] == str(skill)

        connected = client.post("/api/v1/setup/tools/antigravity/connect").json()
        assert connected["connected"] and connected["skill"]
        assert f"mcp add --type http argos {url}" in calls(tools)
        assert "name: argos" in (skill / "SKILL.md").read_text()
        assert (skill / onboarding.MARKER).exists()

        only_mcp = client.post(
            "/api/v1/setup/tools/antigravity/disconnect", json={"mcp": True, "skill": False}
        ).json()
        assert not only_mcp["connected"] and only_mcp["skill"] and skill.exists()
        assert "mcp remove argos" in calls(tools)
        # Removing again: no entry left, so `agy mcp remove` (which would fail) is skipped.
        again = client.post("/api/v1/setup/tools/antigravity/disconnect", json={}).json()
        assert not again["connected"] and not again["skill"] and not skill.exists()
        assert calls(tools).count("mcp remove argos") == 1


def test_antigravity_other_entries_and_their_skill_are_left_alone(
    config: Settings, tools: Path
) -> None:
    theirs = config.antigravity_dir / "skills" / "argos"
    theirs.mkdir(parents=True)
    (theirs / "SKILL.md").write_text("mine")
    status = onboarding.tool_status(config, "antigravity")
    # The notion row in `agy mcp list` is not mistaken for Argos.
    assert not status.connected and status.skill_conflict and not status.skill
    with pytest.raises(onboarding.ConnectError, match="덮어쓰지 않았어요"):
        onboarding.connect(config, "antigravity")
    onboarding.disconnect(config, "antigravity", mcp=True, skill=True)
    assert (theirs / "SKILL.md").read_text() == "mine"


def test_antigravity_refresh_follows_a_new_address(config: Settings, tools: Path) -> None:
    onboarding.connect(config, "antigravity")
    skill = config.antigravity_dir / "skills" / "argos" / "SKILL.md"
    skill.write_text("old version")
    moved = config.model_copy(update={"port": 8123})
    changed = onboarding.refresh(moved)
    assert "antigravity skill" in changed and "antigravity mcp" in changed
    assert "name: argos" in skill.read_text()
    assert onboarding.tool_status(moved, "antigravity").connected
    assert onboarding.refresh(moved) == []


# --- MCP and skill separately ------------------------------------------------------------


def test_mcp_and_skill_connect_and_disconnect_separately(config: Settings, tools: Path) -> None:
    skill = config.claude_dir / "skills" / "argos"
    with TestClient(create_app(config)) as client:
        by_name = {t["name"]: t for t in client.get("/api/v1/setup").json()["tools"]}
        assert by_name["claude"]["skill_supported"] and not by_name["claude"]["mcp_connected"]
        assert not by_name["ollama"]["skill_supported"]

        mcp_only = client.post(
            "/api/v1/setup/tools/claude/connect", json={"mcp": True, "skill": False}
        ).json()
        assert mcp_only["connected"] and mcp_only["mcp_connected"]
        assert not mcp_only["skill"] and not mcp_only["skill_installed"] and not skill.exists()

        skill_only = client.post(
            "/api/v1/setup/tools/codex/connect", json={"mcp": False, "skill": True}
        ).json()
        assert skill_only["skill_installed"] and not skill_only["mcp_connected"]
        assert not any(c.startswith("mcp add") and "codex" in c for c in calls(tools))

        both = client.post("/api/v1/setup/tools/claude/connect", json={"skill": True}).json()
        assert both["mcp_connected"] and both["skill_installed"] and skill.is_dir()
        neither = client.post(
            "/api/v1/setup/tools/claude/connect", json={"mcp": False, "skill": False}
        )
        assert neither.status_code == 422

        only_mcp = client.post(
            "/api/v1/setup/tools/claude/disconnect", json={"mcp": True, "skill": False}
        ).json()
        assert not only_mcp["mcp_connected"] and only_mcp["skill_installed"] and skill.is_dir()


def test_skill_only_conflict_does_not_claim_mcp(config: Settings, tools: Path) -> None:
    theirs = config.claude_dir / "skills" / "argos"
    theirs.mkdir(parents=True)
    (theirs / "SKILL.md").write_text("mine")
    with pytest.raises(onboarding.ConnectError) as raised:
        onboarding.connect(config, "claude", mcp=False, skill=True)
    assert "MCP" not in str(raised.value) and "덮어쓰지 않았어요" in str(raised.value)
    assert not onboarding.tool_status(config, "claude").connected


def test_hermes_skill_only_does_not_touch_its_mcp(config: Settings, tools: Path) -> None:
    status = onboarding.connect(config, "hermes", mcp=False, skill=True)
    assert status.skill and not status.connected
    assert not any(c.startswith("config set mcp_servers") for c in calls(tools))


# --- Antigravity IDE (mcp_config.json) -----------------------------------------------------


OTHERS = {
    "notion": {"serverUrl": "https://mcp.notion.com/mcp", "disabled": False},
    "blender": {"command": "uvx", "args": ["mcp-for-blender"]},
}


def _ide_file(config: Settings, servers: dict[str, object] | None = None, **extra: object) -> Path:
    path = onboarding.ide_config(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": servers or dict(OTHERS), **extra}))
    path.chmod(0o600)
    return path


def test_ide_is_missing_until_its_folder_exists(config: Settings) -> None:
    status = onboarding.tool_status(config, "antigravity_ide")
    assert not status.installed and status.connectable and status.skill_supported
    with pytest.raises(onboarding.ConnectError, match="설치되어 있지 않아요"):
        onboarding.connect(config, "antigravity_ide")
    assert not onboarding.ide_config(config).exists()


def test_ide_connect_keeps_every_other_entry_and_disconnect_removes_only_argos(
    config: Settings, tools: Path
) -> None:
    path = _ide_file(config, theme="dark")
    url = f"http://127.0.0.1:{config.port}/mcp?agent=antigravity"
    skill = config.antigravity_ide_dir / "skills" / "argos"
    with TestClient(create_app(config)) as client:
        ide = {t["name"]: t for t in client.get("/api/v1/setup").json()["tools"]}["antigravity_ide"]
        assert ide["label"] == "Antigravity IDE" and ide["installed"] and not ide["connected"]
        assert ide["path"] == str(config.antigravity_ide_dir) and ide["shared_with"] is None

        done = client.post("/api/v1/setup/tools/antigravity_ide/connect").json()
        assert done["mcp_connected"] and done["skill_installed"]
        data = json.loads(path.read_text())
        assert data["theme"] == "dark"  # keys outside mcpServers stay
        assert data["mcpServers"] == {**OTHERS, "argos": {"serverUrl": url, "disabled": False}}
        assert oct(path.stat().st_mode & 0o777) == "0o600"  # permissions kept
        assert "name: argos" in (skill / "SKILL.md").read_text()
        assert not [p for p in path.parent.iterdir() if p.name.endswith(".tmp")]

        mcp_off = client.post(
            "/api/v1/setup/tools/antigravity_ide/disconnect", json={"mcp": True, "skill": False}
        ).json()
        assert not mcp_off["mcp_connected"] and mcp_off["skill_installed"]
        assert json.loads(path.read_text()) == {"mcpServers": OTHERS, "theme": "dark"}
        before = path.stat().st_mtime_ns
        client.post("/api/v1/setup/tools/antigravity_ide/disconnect", json={})
        assert path.stat().st_mtime_ns == before  # no argos entry: the file is not rewritten
        assert not skill.exists()
    assert not any("antigravity_ide" in c for c in calls(tools))  # no CLI was run for it


def test_ide_without_a_config_file_gets_one(config: Settings) -> None:
    config.antigravity_ide_dir.mkdir(parents=True)
    status = onboarding.connect(config, "antigravity_ide", mcp=True, skill=False)
    assert status.connected and not status.skill
    data = json.loads(onboarding.ide_config(config).read_text())
    assert list(data["mcpServers"]) == ["argos"]


def test_ide_config_that_is_not_json_is_left_alone(config: Settings) -> None:
    path = onboarding.ide_config(config)
    path.parent.mkdir(parents=True)
    path.write_text("{ not json")
    assert not onboarding.tool_status(config, "antigravity_ide").connected
    with pytest.raises(onboarding.ConnectError, match="건드리지 않았어요"):
        onboarding.connect(config, "antigravity_ide")
    with pytest.raises(onboarding.ConnectError, match="건드리지 않았어요"):
        onboarding.disconnect(config, "antigravity_ide", mcp=True, skill=False)
    path.write_text('{"mcpServers": []}')
    with pytest.raises(onboarding.ConnectError, match="형식"):
        onboarding.connect(config, "antigravity_ide")
    assert path.read_text() == '{"mcpServers": []}'


def test_ide_disabled_or_foreign_argos_entry_is_not_connected(config: Settings) -> None:
    url = onboarding.mcp_url(config, "antigravity_ide")
    _ide_file(config, {**OTHERS, "argos": {"serverUrl": url, "disabled": True}})
    assert not onboarding.tool_status(config, "antigravity_ide").connected
    assert onboarding.connect(config, "antigravity_ide", skill=False).connected
    _ide_file(config, {"argos": {"serverUrl": "https://elsewhere.example/mcp"}})
    assert not onboarding.tool_status(config, "antigravity_ide").connected


def test_ide_shares_the_cli_config_through_symlinks(config: Settings, tools: Path) -> None:
    """The common setup: ~/.gemini/antigravity/{mcp_config.json,skills} link into the CLI's
    ~/.gemini/config. Writing follows the links; the links themselves stay."""
    cli = config.antigravity_dir
    (cli / "skills").mkdir(parents=True)
    (cli / "mcp_config.json").write_text(json.dumps({"mcpServers": OTHERS}))
    ide = config.antigravity_ide_dir
    ide.mkdir(parents=True)
    os.symlink(cli / "mcp_config.json", ide / "mcp_config.json")
    os.symlink(cli / "skills", ide / "skills")

    status = onboarding.connect(config, "antigravity_ide")
    assert status.connected and status.skill and status.shared_with == "antigravity"
    assert (ide / "mcp_config.json").is_symlink() and (ide / "skills").is_symlink()
    servers = json.loads((cli / "mcp_config.json").read_text())["mcpServers"]
    # The same address `agy mcp add` would write, so the two never fight over it.
    assert servers["argos"]["serverUrl"] == onboarding.mcp_url(config, "antigravity")
    assert set(servers) == {"notion", "blender", "argos"}
    assert (cli / "skills" / "argos" / onboarding.MARKER).exists()
    assert onboarding.tool_status(config, "antigravity").skill  # the CLI sees the same skill

    onboarding.disconnect(config, "antigravity_ide", mcp=True, skill=True)
    assert set(json.loads((cli / "mcp_config.json").read_text())["mcpServers"]) == {
        "notion",
        "blender",
    }
    assert (ide / "mcp_config.json").is_symlink()


def test_ide_refresh_and_uninstall(config: Settings, tools: Path) -> None:
    path = _ide_file(config)
    onboarding.connect(config, "antigravity_ide")
    moved = config.model_copy(update={"port": 8123})
    assert "antigravity_ide mcp" in onboarding.refresh(moved)
    assert onboarding.tool_status(moved, "antigravity_ide").connected
    assert onboarding.refresh(moved) == []

    removed = onboarding.remove_everywhere(moved)
    assert removed == ["Antigravity IDE: MCP + 스킬 제거"]
    assert json.loads(path.read_text())["mcpServers"] == OTHERS
    assert not (config.antigravity_ide_dir / "skills" / "argos").exists()
