"""Phase 12: first-run setup — tool detection, connecting the agent CLIs to Argos' MCP
server with the argos skill, and removing both, with fake CLIs that record their calls."""

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
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    return log


@pytest.fixture
def config(settings: Settings, tmp_path: Path) -> Settings:
    return settings.model_copy(
        update={
            "claude_dir": tmp_path / "home" / ".claude",
            "codex_auth": tmp_path / "home" / ".codex" / "auth.json",
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
