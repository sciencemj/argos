"""First-run setup (PLAN Phase 12): which agent tools are installed, and connecting them
to Argos — the MCP server (through each tool's own CLI) and the `argos` skill that says
how to use it. Nothing here runs on its own: connecting and removing are buttons the
user presses, since they change the tool's own settings. The MCP entry and the skill can
be added and removed separately; both together is the default.

The Antigravity IDE has no CLI for this: Argos edits its `mcp_config.json` itself, touching
only the `argos` entry, and writes it atomically (through a symlink, which many setups have
pointing at the Antigravity CLI's own file)."""

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from argos.config import Settings

_REPO = Path(__file__).resolve().parents[3]
SKILL = "argos"
MARKER = ".installed-by-argos"  # only skill folders with this are ours to replace or delete


@dataclass
class ToolStatus:
    name: str  # claude | codex | hermes | antigravity | antigravity_ide | ollama
    label: str
    installed: bool
    path: str | None
    version: str | None
    connectable: bool  # Argos can register its MCP server and skill with it
    connected: bool  # the MCP server is registered with this Argos' address
    skill: bool  # the argos skill is installed
    skill_supported: bool  # the tool reads skills, so the argos skill can be installed
    skill_path: str | None
    skill_conflict: bool  # an `argos` skill Argos did not install is in the way
    hint: str  # how to get the tool when it is missing
    # Another tool whose MCP config file this one reads too (the IDE and the CLI often share
    # one through a symlink): connecting either shows on both.
    shared_with: str | None = None


TOOLS = {
    "claude": ("Claude Code", "npm install -g @anthropic-ai/claude-code 후 claude 로그인"),
    "codex": ("Codex", "npm install -g @openai/codex 후 codex login"),
    "hermes": ("Hermes Agent", "Hermes Agent를 설치하고 API 서버를 켜세요 (docs/hermes-setup.md)"),
    "antigravity": ("Antigravity", "Antigravity CLI(agy)를 설치하고 agy로 로그인"),
    "antigravity_ide": ("Antigravity IDE", "Antigravity 앱을 설치하고 한 번 열어 주세요"),
    "ollama": ("Ollama", "https://ollama.com/download 에서 설치 (인박스 분류용 로컬 모델)"),
}


CONNECTABLE = ("claude", "codex", "hermes", "antigravity", "antigravity_ide")
IDE = "antigravity_ide"


class ConnectError(Exception):
    pass


def _binary(name: str, settings: Settings) -> str:
    return {
        "claude": settings.claude_bin,
        "codex": settings.codex_bin,
        "hermes": settings.hermes_bin,
        "antigravity": settings.antigravity_bin,
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


def mcp_url(settings: Settings, name: str) -> str:
    # The IDE is the same Antigravity agent to Argos. It also has to be the same address:
    # with a shared config file, two would overwrite each other on every refresh.
    agent = "antigravity" if name == IDE else name
    return f"{settings.mcp_url}?agent={agent}"


# --- where the skill lives -----------------------------------------------------------------


def _bundled(relative: str) -> Path:
    """The skill as shipped: in the checkout, or unpacked by the app (temporary)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", "")) / relative
    return _REPO / relative


def _skill_home(name: str, settings: Settings) -> Path:
    """Claude Code, Codex and Antigravity read user skills from <home>/skills/<name>/SKILL.md."""
    homes = {
        "claude": settings.claude_dir,
        "antigravity": settings.antigravity_dir,
        IDE: settings.antigravity_ide_dir,
    }
    return homes.get(name, settings.codex_auth.parent) / "skills"


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


# --- the Antigravity IDE's mcp_config.json --------------------------------------------------


def ide_config(settings: Settings) -> Path:
    return settings.antigravity_ide_dir / "mcp_config.json"


def _cli_config(settings: Settings) -> Path:
    """Where `agy mcp add` writes (the CLI's own config folder)."""
    return settings.antigravity_dir / "mcp_config.json"


def _read_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        text = path.read_text()
        data: Any = json.loads(text) if text.strip() else {}
    except (OSError, ValueError) as exc:
        raise ConnectError(f"{path}을 읽지 못해 건드리지 않았어요: {exc}") from exc
    if not isinstance(data, dict):
        raise ConnectError(f"{path}의 형식이 예상과 달라 건드리지 않았어요")
    config: dict[str, Any] = data  # pyright: ignore[reportUnknownVariableType]
    if not isinstance(config.get("mcpServers", {}), dict):
        raise ConnectError(f"{path}의 형식이 예상과 달라 건드리지 않았어요")
    return config


def _write_config(path: Path, data: dict[str, Any]) -> None:
    """Atomic: a temp file next to the real one, then a rename. A symlink is followed, so
    the link stays and the file it points at is replaced; the file keeps its permissions."""
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o644
    fd, temp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(data, file, indent=2)
            file.write("\n")
        os.chmod(temp, mode)
        os.replace(temp, target)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def _ide_entry(settings: Settings) -> dict[str, Any] | None:
    servers: dict[str, Any] = _read_config(ide_config(settings)).get("mcpServers", {})
    entry = servers.get(SKILL)
    return entry if isinstance(entry, dict) else None  # pyright: ignore[reportUnknownVariableType]


def _ide_register(settings: Settings, url: str) -> None:
    path = ide_config(settings)
    data = _read_config(path)
    servers: dict[str, Any] = data.setdefault("mcpServers", {})
    servers[SKILL] = {"serverUrl": url, "disabled": False}  # what `agy mcp add` writes
    _write_config(path, data)


def _ide_unregister(settings: Settings) -> None:
    path = ide_config(settings)
    data = _read_config(path)
    servers: dict[str, Any] = data.get("mcpServers", {})
    if SKILL in servers:
        del servers[SKILL]
        _write_config(path, data)


def _ide_shared_with(settings: Settings) -> str | None:
    """The CLI, when the IDE's config file is (a link to) the one `agy` writes."""
    try:
        same = ide_config(settings).resolve() == _cli_config(settings).resolve()
    except OSError:
        return None
    return "antigravity" if same and ide_config(settings).exists() else None


# --- status ----------------------------------------------------------------------------------


def _version(binary: str) -> str | None:
    try:
        done = _run([binary, "--version"], timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = (done.stdout or done.stderr).strip().splitlines()
    # e.g. "Hermes Agent v0.21.1 (2026.9.7) · upstream …": the part before build details
    return lines[0].split(" · ")[0][:60] if lines else None


def _mcp_query(name: str, binary: str) -> list[str]:
    """The command that prints the tool's Argos MCP entry (Antigravity: its whole list)."""
    return {
        "claude": [binary, "mcp", "get", SKILL],
        "codex": [binary, "mcp", "get", SKILL, "--json"],
        "hermes": [binary, "config", "get", "mcp_servers.argos.url"],
        "antigravity": [binary, "mcp", "list"],
    }[name]


def _mcp_entry(settings: Settings, name: str, binary: str) -> str | None:
    """What the tool says about its `argos` MCP server, or None when it has none."""
    if name == IDE:
        try:
            entry = _ide_entry(settings)
        except ConnectError:
            return None
        return json.dumps(entry) if entry is not None else None
    try:
        done = _run(_mcp_query(name, binary))
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    if name != "antigravity":
        return done.stdout
    # `agy mcp list`: a table, one server per line, its name first.
    rows = [line for line in done.stdout.splitlines() if line.split()[:1] == [SKILL]]
    return rows[0] if rows else None


def _connected(settings: Settings, name: str, binary: str) -> bool:
    url = mcp_url(settings, name)
    if name == IDE:
        try:
            entry = _ide_entry(settings)
        except ConnectError:
            return False
        return entry is not None and entry.get("serverUrl") == url and not entry.get("disabled")
    entry = _mcp_entry(settings, name, binary)
    return entry is not None and url in entry


def _locate(settings: Settings, name: str) -> str | None:
    """The tool's binary, or for the IDE its settings folder; None when it is not here."""
    if name == IDE:
        folder = settings.antigravity_ide_dir
        return str(folder) if folder.is_dir() else None
    return shutil.which(_binary(name, settings))


def tool_status(settings: Settings, name: str) -> ToolStatus:
    label, hint = TOOLS[name]
    path = _locate(settings, name)
    connectable = name in CONNECTABLE
    status = ToolStatus(
        name=name,
        label=label,
        installed=path is not None,
        path=path,
        version=_version(path) if path and name != IDE else None,
        connectable=connectable,
        connected=False,
        skill=False,
        skill_supported=connectable,
        skill_path=None,
        skill_conflict=False,
        hint=hint,
    )
    if path is None or not status.connectable:
        return status
    status.connected = _connected(settings, name, path)
    if name == IDE:
        status.shared_with = _ide_shared_with(settings)
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
    if name not in CONNECTABLE:
        raise ConnectError(f"{name}은 연결할 수 없어요")
    binary = _locate(settings, name)
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


def _register_mcp(settings: Settings, binary: str, name: str, url: str) -> None:
    if name == IDE:
        _ide_register(settings, url)
    elif name == "claude":
        _run([binary, "mcp", "remove", SKILL, "-s", "user"])  # absent is fine
        _step([binary, "mcp", "add", "--transport", "http", "--scope", "user", SKILL, url])
    elif name == "codex":
        _run([binary, "mcp", "remove", SKILL])
        _step([binary, "mcp", "add", SKILL, "--url", url])
    elif name == "antigravity":
        # `agy mcp add` adds or updates; flags must come before the name.
        _step([binary, "mcp", "add", "--type", "http", SKILL, url])
    else:
        _step([binary, "config", "set", "mcp_servers.argos.url", url])


def _install_skill(settings: Settings, binary: str, name: str, after_mcp: bool) -> None:
    if name == "hermes":
        folder = hermes_skills_dir(settings)
        if getattr(sys, "frozen", False):
            shutil.copytree(_bundled("integrations/hermes/skills"), folder, dirs_exist_ok=True)
        dirs = _hermes_skill_dirs(binary)
        if str(folder) not in dirs:
            _set_hermes_skill_dirs(binary, [*dirs, str(folder)])
        return
    target = _skill_home(name, settings) / SKILL
    if target.exists() and not (target / MARKER).exists():
        done = "MCP는 연결했지만 " if after_mcp else ""
        raise ConnectError(f"{done}{target}에 다른 argos 스킬이 있어 덮어쓰지 않았어요")
    shutil.copytree(_bundled(f"integrations/skills/{SKILL}"), target, dirs_exist_ok=True)
    (target / MARKER).write_text("Installed by the Argos app; removed from its settings.\n")


def connect(settings: Settings, name: str, mcp: bool = True, skill: bool = True) -> ToolStatus:
    """Registers Argos' MCP server (replacing an older Argos entry that points elsewhere)
    and/or installs the argos skill. Hermes then restarts its gateway to load them."""
    binary = _usable(settings, name)
    if mcp:
        _register_mcp(settings, binary, name, mcp_url(settings, name))
    if skill:
        _install_skill(settings, binary, name, after_mcp=mcp)
    if name == "hermes" and (mcp or skill):
        _restart_hermes(binary)
    return tool_status(settings, name)


_ARGOS_URL = re.compile(r"https?://[^\s\"']+/mcp\?agent=[\w-]+")


def _registered_url(settings: Settings, name: str, binary: str) -> str | None:
    """The Argos MCP address the tool has now, if it has one."""
    entry = _mcp_entry(settings, name, binary)
    found = _ARGOS_URL.search(entry) if entry is not None else None
    return found.group(0) if found else None


def refresh(settings: Settings) -> list[str]:
    """After an update (at every app start): brings what Argos installed up to date — its
    skill copies when the bundled skill changed, and an Argos MCP entry that points at an
    old address. Nothing is added that the user did not connect or has removed."""
    changed: list[str] = []
    for name in CONNECTABLE:
        binary = _locate(settings, name)
        if binary is None:
            continue
        try:
            if name == "hermes":
                folder = hermes_skills_dir(settings)
                if getattr(sys, "frozen", False) and folder.is_dir():
                    shutil.copytree(
                        _bundled("integrations/hermes/skills"), folder, dirs_exist_ok=True
                    )
            else:
                target = _skill_home(name, settings) / SKILL
                source = _bundled(f"integrations/skills/{SKILL}") / "SKILL.md"
                installed = target / "SKILL.md"
                if (target / MARKER).exists() and (
                    not installed.exists() or installed.read_bytes() != source.read_bytes()
                ):
                    shutil.copytree(source.parent, target, dirs_exist_ok=True)
                    changed.append(f"{name} skill")
            url = mcp_url(settings, name)
            current = _registered_url(settings, name, binary)
            if current is not None and current != url:
                _register_mcp(settings, binary, name, url)
                changed.append(f"{name} mcp")
                if name == "hermes":
                    _restart_hermes(binary)
        except (OSError, ConnectError, subprocess.TimeoutExpired):
            continue  # try again at the next start
    return changed


def disconnect(settings: Settings, name: str, mcp: bool, skill: bool) -> ToolStatus:
    """Removes what connect added: the MCP entry and/or the skill (only a skill folder
    Argos installed; Hermes just stops loading Argos' folder)."""
    binary = _usable(settings, name)
    if mcp:
        if name == IDE:
            _ide_unregister(settings)
        elif name == "claude":
            _step([binary, "mcp", "remove", SKILL, "-s", "user"])
        elif name == "codex":
            _step([binary, "mcp", "remove", SKILL])
        elif name == "antigravity":
            if _mcp_entry(settings, name, binary) is not None:  # `agy mcp remove` fails when absent
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
    if name == "hermes" and (mcp or skill):
        _restart_hermes(binary)
    return tool_status(settings, name)


def remove_everywhere(settings: Settings) -> list[str]:
    """Uninstall: takes Argos out of every agent tool — its MCP entry (also one left at an
    old address) and the skill Argos installed. Returns what was removed or failed."""
    report: list[str] = []
    for name in CONNECTABLE:
        binary = _locate(settings, name)
        if binary is None:
            continue
        status = tool_status(settings, name)
        mcp = status.connected or _registered_url(settings, name, binary) is not None
        if not (mcp or status.skill):
            continue
        try:
            disconnect(settings, name, mcp=mcp, skill=status.skill)
            report.append(
                f"{TOOLS[name][0]}: "
                + " + ".join(part for part, on in (("MCP", mcp), ("스킬", status.skill)) if on)
                + " 제거"
            )
        except (OSError, ConnectError, subprocess.TimeoutExpired) as exc:
            report.append(f"{TOOLS[name][0]}: 제거 실패 ({exc})")
    return report
