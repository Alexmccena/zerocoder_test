from __future__ import annotations

from datetime import datetime

from trading_bot.domain.models import AccountState, ExecutionPlan, ExecutionResult, MarketSnapshot, PositionState
from trading_bot.domain.protocols import ExecutionVenue


class ExecutionEngine:
    def __init__(self, *, venue: ExecutionVenue) -> None:
        self.venue = venue

    async def submit(self, plan: ExecutionPlan) -> ExecutionResult:
        return await self.venue.submit(plan)

    async def on_market_event(self, *, symbol: str, snapshot: MarketSnapshot, as_of: datetime) -> ExecutionResult:
        return await self.venue.process_market_event(symbol, snapshot, as_of)

    async def sync_positions(self) -> list[PositionState]:
        return await self.venue.sync_positions()

    def account_state(self) -> AccountState:
        return self.venue.account_state()
