from functools import cached_property
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="ARGOS_", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8000
    # Everything Argos writes lives here; relative paths below are inside it. The desktop
    # app sets ~/Library/Application Support/Argos, a checkout keeps backend/data.
    data_dir: Path = Path("data")
    db_path: Path = Path("argos.db")
    # The built frontend (frontend/dist). When set, the backend serves the app itself on
    # its own port (the desktop app); in development Vite serves it instead.
    static_dir: Path | None = None
    # Set by the desktop app: starting at login registers the app, not `make dev`.
    desktop_app: Path | None = None
    vault_path: Path | None = None
    # Demo areas/channels loaded into an empty database on first run (PLAN §9).
    seed_path: Path = Path("seed.example.toml")
    timezone: str = "Asia/Seoul"
    language: Literal["ko", "en"] = "ko"
    # Address other devices use to reach Argos (e.g. the Tailscale serve URL); links such as
    # the calendar feed use it instead of whatever address the browser happened to open.
    public_url: str | None = None
    # "Today" lists open tasks that are overdue or due within this many days.
    due_soon_days: int = 3
    # Kanban warns (never blocks) when In Progress holds more cards than this.
    wip_limit: int = 3

    # Inbox classifier (PLAN §9: model is the user's choice; empty = classification off).
    classifier_provider: Literal["ollama", "hermes"] = "ollama"
    classifier_model: str = ""
    classifier_base_url: str | None = None  # default per provider, see classifier.py
    classifier_api_key: SecretStr | None = None  # Hermes needs one; never logged
    classifier_timeout: float = 60.0
    # Thinking models (e.g. qwen3.5) can reason for minutes before answering; a one-line
    # classification does not need it. Unset = "none" for ollama, omitted for hermes.
    classifier_reasoning_effort: Literal["none", "low", "medium", "high"] | None = None
    # Suggestions at or above this confidence are applied without asking, but only for
    # the types listed in classifier_auto_apply (empty by default: always ask).
    classifier_threshold: float = 0.85
    classifier_auto_apply: list[Literal["task", "event"]] = []

    # Agents (PLAN Phase 5). The default agent is changed in the app (app_setting).
    default_agent: str = "hermes"
    hermes_base_url: str = "http://127.0.0.1:8642/v1"
    # API_SERVER_KEY; unset: read from Hermes' own .env (hermes_home) when it is there.
    hermes_api_key: SecretStr | None = None
    hermes_home: Path = Path.home() / ".hermes"
    hermes_model: str = "hermes-agent"
    claude_bin: str = "claude"
    codex_bin: str = "codex"
    # "app-server": JSON-RPC, token streaming, persistent threads (experimental protocol);
    # "exec": the older one-shot `codex exec --json`, kept as a fallback.
    codex_mode: Literal["app-server", "exec"] = "app-server"
    # Codex's home for Argos runs: only auth.json is linked from the user's ~/.codex, so
    # their config and MCP servers stay out and Argos threads stay out of their history.
    codex_home: Path = Path("codex-home")
    codex_auth: Path = Path.home() / ".codex" / "auth.json"
    # Plan usage (PLAN Phase 9): read every few minutes (0 turns the timer off). Claude's
    # comes with the login Claude Code saved (Keychain on a Mac, else claude_dir).
    claude_dir: Path = Path.home() / ".claude"
    usage_poll_minutes: int = 5

    # Empty working directory for Claude Code/Codex chat runs: they get Argos tools only.
    agent_workspace: Path = Path("agent-workspace")
    agent_timeout: float = 300.0  # seconds before a chat run is stopped
    # Coding jobs (PLAN Phase 6, §9: allowed directories are the user's choice). A job
    # may only write inside one of these roots; without --dir it gets a fresh folder in
    # the first one.
    # iCloud calendars over CalDAV (PLAN 7b). The Apple ID is set in the app; the
    # app-specific password is kept in the macOS Keychain.
    caldav_url: str = "https://caldav.icloud.com/"
    caldav_write_calendar: str = "Argos"  # the only calendar Argos writes to
    caldav_poll_minutes: int = 10

    # Obsidian vault (PLAN Phase 8). Usually picked in the settings screen; the daily
    # notes folder defaults to the vault's own Daily Notes setting.
    vault_path: Path | None = None
    vault_daily_folder: str | None = None
    vault_daily_days: int = 14  # open tasks in daily notes older than this are left out
    vault_backup_dir: Path = Path("vault-backups")

    # Debates (PLAN Phase 10): the whole debate must end within this many seconds.
    debate_budget_seconds: int = 900

    # Proactive notices and reviews (PLAN Phase 11). Messenger delivery goes through
    # `hermes send` and is turned on in the settings screen.
    notify_interval_minutes: int = 15  # 0 turns the checks off
    notify_digest_hour: int = 9  # daily roundups (stale inbox, undated tasks) after this hour
    notify_hermes_target: str | None = None  # e.g. "discord" or "discord:#general"
    hermes_bin: str = "hermes"
    inbox_stale_hours: int = 24
    undated_after_days: int = 3
    backlog_stale_days: int = 14
    weekly_review_weekday: int = 6  # Monday 0 … Sunday 6
    weekly_review_hour: int = 20
    review_embed_model: str | None = None  # Ollama embedding model; None: find one installed
    backup_keep: int = 14  # daily database backups kept in data/backups
    auto_backup: bool = True
    backup_dir: Path = Path("backups")

    job_roots: list[Path] = [Path("jobs")]
    job_concurrency: int = 1  # jobs running at once; the rest wait as "queued"
    job_timeout: float = 1800.0
    job_max_budget_usd: float = 2.0  # Claude jobs stop past this API-cost estimate
    context_limit: int = 4000  # characters of channel context sent with each run
    dm_session_idle_hours: float = 6.0  # a DM message after this long starts a new conversation

    @model_validator(mode="after")
    def _inside_data_dir(self) -> "Settings":
        """Relative data paths are inside data_dir, whatever the working directory."""
        for name in ("db_path", "codex_home", "agent_workspace", "vault_backup_dir", "backup_dir"):
            path: Path = getattr(self, name)
            if not path.is_absolute():
                setattr(self, name, self.data_dir / path)
        self.job_roots = [p if p.is_absolute() else self.data_dir / p for p in self.job_roots]
        if self.hermes_api_key is None:
            self.hermes_api_key = _hermes_key(self.hermes_home / ".env")
        return self

    @property
    def mcp_url(self) -> str:
        return f"http://{self.host}:{self.port}/mcp"

    @property
    def db_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.db_path}"

    @cached_property
    def zoneinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


def _hermes_key(env_file: Path) -> SecretStr | None:
    """Hermes keeps its API server key in its own .env; read it (never written, never
    logged) so a fresh install needs no copy of it."""
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and key.strip() == "API_SERVER_KEY" and value.strip():
            return SecretStr(value.strip().strip("\"'"))
    return None


settings = Settings()
