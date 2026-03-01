from __future__ import annotations

import time
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from trading_bot.bootstrap.settings import project_root


def build_async_engine(postgres_dsn: str) -> AsyncEngine:
    return create_async_engine(postgres_dsn, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def ping_database(engine: AsyncEngine) -> float:
    started_at = time.perf_counter()
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    return time.perf_counter() - started_at


def build_alembic_config(postgres_dsn: str) -> Config:
    config_path = Path(project_root()) / "alembic.ini"
    config = Config(str(config_path))
    config.set_main_option("script_location", str(Path(project_root()) / "alembic"))
    config.set_main_option("sqlalchemy.url", postgres_dsn)
    return config


def run_alembic_upgrade(postgres_dsn: str, revision: str = "head") -> None:
    command.upgrade(build_alembic_config(postgres_dsn), revision)


def run_alembic_current(postgres_dsn: str) -> None:
    command.current(build_alembic_config(postgres_dsn))
