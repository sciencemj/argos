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

    @property
    def db_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.db_path}"

    @cached_property
    def zoneinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


settings = Settings()
