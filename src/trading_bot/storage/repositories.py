from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading_bot.storage.models import ConfigSnapshotRecord, RunSessionRecord


@dataclass(slots=True)
class RunSessionRepository:
    session_factory: async_sessionmaker[AsyncSession]

    async def create(self, *, run_mode: str, environment: str, status: str) -> RunSessionRecord:
        record = RunSessionRecord(run_mode=run_mode, environment=environment, status=status)
        async with self.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record


@dataclass(slots=True)
class ConfigSnapshotRepository:
    session_factory: async_sessionmaker[AsyncSession]

    async def create(self, *, run_session_id: str | None, config_hash: str, config_json: dict) -> ConfigSnapshotRecord:
        record = ConfigSnapshotRecord(
            run_session_id=run_session_id,
            config_hash=config_hash,
            config_json=config_json,
        )
        async with self.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record
