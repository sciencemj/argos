# PyInstaller spec for the Argos server sidecar (PLAN Phase 12). Built by `make app`:
#   cd backend && uv run --group desktop pyinstaller ../desktop/argos-server.spec
# One file: the Tauri shell starts it as `binaries/argos-server-<target triple>`.
# ruff: noqa
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent  # noqa: F821 (PyInstaller global)
BACKEND = ROOT / "backend"

hidden = [
    *collect_submodules("argos"),
    *collect_submodules("uvicorn"),
    *collect_submodules("keyring.backends"),
    *collect_submodules("sqlalchemy.dialects.sqlite"),
    "aiosqlite",
]
datas = [
    (str(BACKEND / "alembic"), "alembic"),
    (str(BACKEND / "seed.example.toml"), "."),
    (str(ROOT / "frontend" / "dist"), "web"),
    (str(ROOT / "integrations" / "hermes" / "skills"), "integrations/hermes/skills"),
    (str(ROOT / "integrations" / "skills"), "integrations/skills"),
    *collect_data_files("mcp"),
    *collect_data_files("icalendar"),
]

a = Analysis(  # noqa: F821
    [str(BACKEND / "src" / "argos" / "desktop.py")],
    pathex=[str(BACKEND / "src")],
    hiddenimports=hidden,
    datas=datas,
    # Claude Code ships its own CLI inside the SDK (~200 MB); Argos runs the user's
    # installed `claude` instead (agents.py passes cli_path), so leave it out.
    excludes=["tkinter", "pytest", "pyright", "ruff"],
)
a.datas = [d for d in a.datas if "claude_agent_sdk/_bundled" not in d[0]]
a.binaries = [b for b in a.binaries if "claude_agent_sdk/_bundled" not in b[0]]
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="argos-server",
    console=True,
    upx=False,
    target_arch="arm64",
)
