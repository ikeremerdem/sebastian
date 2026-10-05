"""Environment-driven configuration. Secrets and paths live outside git."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SEBASTIAN_", env_file=".env", extra="ignore")

    database_path: Path = Path("data/sebastian.db")
    api_key: str = ""
    ui_password_hash: str = ""
    session_secret: str = ""
    timezone: str = "UTC"
    default_nag_interval_min: int = 60
    quiet_hours: str = "22:00-07:00"
    auto_migrate: bool = True
    host: str = "127.0.0.1"
    port: int = 8000

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path}"


@lru_cache
def get_config() -> Config:
    return Config()
