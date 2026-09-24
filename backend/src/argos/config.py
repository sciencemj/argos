from functools import cached_property
from pathlib import Path
from zoneinfo import ZoneInfo

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

    @property
    def db_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.db_path}"

    @cached_property
    def zoneinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


settings = Settings()
