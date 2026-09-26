"""First-run setup (PLAN Phase 12): which agent tools are installed, and connecting them
to Argos — the MCP server (through each tool's own CLI) and the `argos` skill that says
how to use it. Nothing here runs on its own: connecting and removing are buttons the
user presses, since they change the tool's own settings."""

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from argos.config import Settings

_REPO = Path(__file__).resolve().parents[3]
SKILL = "argos"
MARKER = ".installed-by-argos"  # only skill folders with this are ours to replace or delete


@dataclass
class ToolStatus:
    name: str  # claude | codex | hermes | ollama
    label: str
    installed: bool
    path: str | None
    version: str | None
    connectable: bool  # Argos can register its MCP server and skill with it
    connected: bool  # the MCP server is registered with this Argos' address
    skill: bool  # the argos skill is installed
    skill_path: str | None
    skill_conflict: bool  # an `argos` skill Argos did not install is in the way
    hint: str  # how to get the tool when it is missing


TOOLS = {
    "claude": ("Claude Code", "npm install -g @anthropic-ai/claude-code 후 claude 로그인"),
    "codex": ("Codex", "npm install -g @openai/codex 후 codex login"),
    "hermes": ("Hermes Agent", "Hermes Agent를 설치하고 API 서버를 켜세요 (docs/hermes-setup.md)"),
    "ollama": ("Ollama", "https://ollama.com/download 에서 설치 (인박스 분류용 로컬 모델)"),
}


class ConnectError(Exception):
    pass


def _binary(name: str, settings: Settings) -> str:
    return {
        "claude": settings.claude_bin,
        "codex": settings.codex_bin,
        "hermes": settings.hermes_bin,
    }.get(name, name)


def _run(args: list[str], timeout: float = 20) -> subprocess.CompletedProcess[str]:
    # From the home folder: `claude mcp get` would otherwise also read a project's .mcp.json.
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=Path.home(),
        stdin=subprocess.DEVNULL,
    )


def _step(args: list[str]) -> None:
    try:
        done = _run(args, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConnectError(f"{' '.join(args[1:3])} 실행 실패: {exc}") from exc
    if done.returncode != 0:
        lines = (done.stderr or done.stdout).strip().splitlines()
        raise ConnectError(lines[-1][:300] if lines else f"{args[1]} 실패")


def mcp_url(settings: Settings, agent: str) -> str:
    return f"{settings.mcp_url}?agent={agent}"


# --- where the skill lives -----------------------------------------------------------------


def _bundled(relative: str) -> Path:
    """The skill as shipped: in the checkout, or unpacked by the app (temporary)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", "")) / relative
    return _REPO / relative


def _skill_home(name: str, settings: Settings) -> Path:
    """Claude Code and Codex read user skills from <home>/skills/<name>/SKILL.md."""
    return (settings.claude_dir if name == "claude" else settings.codex_auth.parent) / "skills"


def hermes_skills_dir(settings: Settings) -> Path:
    """The folder Hermes is pointed at (skills.external_dirs): the checkout's copy, or in
    the app a copy in the data folder, refreshed on every connect."""
    bundled = _bundled("integrations/hermes/skills")
    if not getattr(sys, "frozen", False):
        return bundled
    return settings.data_dir.resolve() / "hermes-skills"


def _hermes_skill_dirs(binary: str) -> list[str]:
    done = _run([binary, "config", "get", "skills.external_dirs"])
    if done.returncode != 0:
        return []
    return [
        line.strip()[2:].strip()
        for line in done.stdout.splitlines()
        if line.strip().startswith("- ")
    ]


def _set_hermes_skill_dirs(binary: str, dirs: list[str]) -> None:
    if dirs:
        listed = ", ".join(f'"{d}"' for d in dirs)
        _step([binary, "config", "set", "skills.external_dirs", f"[{listed}]"])
    else:
        _step([binary, "config", "unset", "skills.external_dirs"])


# --- status ----------------------------------------------------------------------------------


def _version(binary: str) -> str | None:
    try:
        done = _run([binary, "--version"], timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = (done.stdout or done.stderr).strip().splitlines()
    # e.g. "Hermes Agent v0.21.1 (2026.9.7) · upstream …": the part before build details
    return lines[0].split(" · ")[0][:60] if lines else None


def _connected(name: str, binary: str, url: str) -> bool:
    commands = {
        "claude": [binary, "mcp", "get", SKILL],
        "codex": [binary, "mcp", "get", SKILL, "--json"],
        "hermes": [binary, "config", "get", "mcp_servers.argos.url"],
    }
    try:
        done = _run(commands[name])
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0 and url in done.stdout


def tool_status(settings: Settings, name: str) -> ToolStatus:
    label, hint = TOOLS[name]
    path = shutil.which(_binary(name, settings))
    status = ToolStatus(
        name=name,
        label=label,
        installed=path is not None,
        path=path,
        version=_version(path) if path else None,
        connectable=name != "ollama",
        connected=False,
        skill=False,
        skill_path=None,
        skill_conflict=False,
        hint=hint,
    )
    if path is None or not status.connectable:
        return status
    status.connected = _connected(name, path, mcp_url(settings, name))
    if name == "hermes":
        folder = hermes_skills_dir(settings)
        status.skill_path = str(folder / SKILL)
        try:
            status.skill = str(folder) in _hermes_skill_dirs(path)
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        folder = _skill_home(name, settings) / SKILL
        status.skill_path = str(folder)
        status.skill = (folder / MARKER).exists()
        status.skill_conflict = folder.exists() and not status.skill
    return status


def statuses(settings: Settings) -> list[ToolStatus]:
    return [tool_status(settings, name) for name in TOOLS]


# --- connect / remove --------------------------------------------------------------------------


def _usable(settings: Settings, name: str) -> str:
    if name not in TOOLS or name == "ollama":
        raise ConnectError(f"{name}은 연결할 수 없어요")
    binary = shutil.which(_binary(name, settings))
    if binary is None:
        raise ConnectError(f"{TOOLS[name][0]}이 설치되어 있지 않아요")
    return binary


def _restart_hermes(binary: str) -> None:
    # A running gateway reads its config at start; if it runs some other way the restart
    # may fail, and its next start picks the settings up anyway.
    try:
        _run([binary, "gateway", "restart"], timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        pass


def connect(settings: Settings, name: str) -> ToolStatus:
    """Registers Argos' MCP server (replacing an older Argos entry that points elsewhere)
    and installs the argos skill. Hermes then restarts its gateway to load both."""
    binary = _usable(settings, name)
    url = mcp_url(settings, name)
    if name == "claude":
        _run([binary, "mcp", "remove", SKILL, "-s", "user"])  # absent is fine
        _step([binary, "mcp", "add", "--transport", "http", "--scope", "user", SKILL, url])
    elif name == "codex":
        _run([binary, "mcp", "remove", SKILL])
        _step([binary, "mcp", "add", SKILL, "--url", url])
    else:
        _step([binary, "config", "set", "mcp_servers.argos.url", url])

    if name == "hermes":
        folder = hermes_skills_dir(settings)
        if getattr(sys, "frozen", False):
            shutil.copytree(_bundled("integrations/hermes/skills"), folder, dirs_exist_ok=True)
        dirs = _hermes_skill_dirs(binary)
        if str(folder) not in dirs:
            _set_hermes_skill_dirs(binary, [*dirs, str(folder)])
        _restart_hermes(binary)
    else:
        target = _skill_home(name, settings) / SKILL
        if target.exists() and not (target / MARKER).exists():
            raise ConnectError(
                f"MCP는 연결했지만 {target}에 다른 argos 스킬이 있어 덮어쓰지 않았어요"
            )
        shutil.copytree(_bundled(f"integrations/skills/{SKILL}"), target, dirs_exist_ok=True)
        (target / MARKER).write_text("Installed by the Argos app; removed from its settings.\n")
    return tool_status(settings, name)


def disconnect(settings: Settings, name: str, mcp: bool, skill: bool) -> ToolStatus:
    """Removes what connect added: the MCP entry and/or the skill (only a skill folder
    Argos installed; Hermes just stops loading Argos' folder)."""
    binary = _usable(settings, name)
    if mcp:
        if name == "claude":
            _step([binary, "mcp", "remove", SKILL, "-s", "user"])
        elif name == "codex":
            _step([binary, "mcp", "remove", SKILL])
        else:
            _step([binary, "config", "unset", "mcp_servers.argos"])
    if skill:
        if name == "hermes":
            folder = str(hermes_skills_dir(settings))
            dirs = _hermes_skill_dirs(binary)
            if folder in dirs:
                _set_hermes_skill_dirs(binary, [d for d in dirs if d != folder])
        else:
            target = _skill_home(name, settings) / SKILL
            if (target / MARKER).exists():
                shutil.rmtree(target)
    if name == "hermes":
        _restart_hermes(binary)
    return tool_status(settings, name)
