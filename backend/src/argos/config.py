from functools import cached_property
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="ARGOS_", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8000
    db_path: Path = Path("data/argos.db")
    vault_path: Path | None = None
    # Demo areas/channels loaded into an empty database on first run (PLAN §9).
    seed_path: Path = Path("seed.example.toml")
    timezone: str = "Asia/Seoul"
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
    hermes_api_key: SecretStr | None = None  # API_SERVER_KEY from ~/.hermes/.env
    hermes_model: str = "hermes-agent"
    claude_bin: str = "claude"
    codex_bin: str = "codex"
    # "app-server": JSON-RPC, token streaming, persistent threads (experimental protocol);
    # "exec": the older one-shot `codex exec --json`, kept as a fallback.
    codex_mode: Literal["app-server", "exec"] = "app-server"
    # Codex's home for Argos runs: only auth.json is linked from the user's ~/.codex, so
    # their config and MCP servers stay out and Argos threads stay out of their history.
    codex_home: Path = Path("data/codex-home")
    codex_auth: Path = Path.home() / ".codex" / "auth.json"
    # Empty working directory for Claude Code/Codex chat runs: they get Argos tools only.
    agent_workspace: Path = Path("data/agent-workspace")
    agent_timeout: float = 300.0  # seconds before a chat run is stopped
    # Coding jobs (PLAN Phase 6, §9: allowed directories are the user's choice). A job
    # may only write inside one of these roots; without --dir it gets a fresh folder in
    # the first one.
    # iCloud calendars over CalDAV (PLAN 7b). The Apple ID is set in the app; the
    # app-specific password is kept in the macOS Keychain.
    caldav_url: str = "https://caldav.icloud.com/"
    caldav_write_calendar: str = "Argos"  # the only calendar Argos writes to
    caldav_poll_minutes: int = 10

    job_roots: list[Path] = [Path("data/jobs")]
    job_concurrency: int = 1  # jobs running at once; the rest wait as "queued"
    job_timeout: float = 1800.0
    job_max_budget_usd: float = 2.0  # Claude jobs stop past this API-cost estimate
    context_limit: int = 4000  # characters of channel context sent with each run

    @property
    def mcp_url(self) -> str:
        return f"http://{self.host}:{self.port}/mcp"

    @property
    def db_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.db_path}"

    @cached_property
    def zoneinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


settings = Settings()
