from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="ARGOS_", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8000
    db_path: Path = Path("data/argos.db")
    vault_path: Path | None = None

    @property
    def db_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.db_path}"


settings = Settings()
