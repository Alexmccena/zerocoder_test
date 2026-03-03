from __future__ import annotations

import asyncio
from datetime import datetime, timezone


class WallClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    async def sleep_until(self, dt: datetime) -> None:
        delay = (dt - self.now()).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)


class ReplayClock:
    def __init__(self, *, speed: float) -> None:
        self.speed = speed
        self._current: datetime | None = None

    def now(self) -> datetime:
        return self._current or datetime.now(timezone.utc)

    async def sleep_until(self, dt: datetime) -> None:
        if self._current is None:
            self._current = dt
            return
        delay = (dt - self._current).total_seconds() / self.speed
        if delay > 0:
            await asyncio.sleep(delay)
        self._current = dt


class BacktestClock:
    def __init__(self) -> None:
        self._current: datetime | None = None

    def now(self) -> datetime:
        return self._current or datetime.now(timezone.utc)

    async def sleep_until(self, dt: datetime) -> None:
        self._current = dt
