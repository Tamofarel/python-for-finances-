"""Run Alembic migrations programmatically (used at startup and by scripts)."""

from __future__ import annotations

from alembic import command
from alembic.config import Config

from app.core.config import BACKEND_DIR


def alembic_config(url: str) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def upgrade(url: str) -> None:
    command.upgrade(alembic_config(url), "head")


if __name__ == "__main__":
    from app.core.config import get_settings

    s = get_settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    upgrade(s.resolved_database_url)
    print("Database is at the latest migration.")
