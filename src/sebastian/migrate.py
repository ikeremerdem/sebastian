"""Run Alembic migrations programmatically (used on startup and by `sebastian migrate`)."""

from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig

from .config import get_config

_MIGRATIONS = Path(__file__).parent / "migrations"


def alembic_config(database_url: str | None = None) -> AlembicConfig:
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(_MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", database_url or get_config().database_url)
    return cfg


def upgrade_to_head(database_url: str | None = None) -> None:
    if database_url is None:
        get_config().database_path.parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(alembic_config(database_url), "head")
