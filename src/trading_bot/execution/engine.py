from __future__ import annotations

from datetime import datetime

from trading_bot.config.schema import AppSettings
from trading_bot.domain.models import AccountState, BracketState, ExecutionPlan, ExecutionResult, MarketSnapshot, PositionState, VenueStateSnapshot
from trading_bot.domain.protocols import ExecutionVenue
from trading_bot.execution.bracket_manager import BracketManager


def _merge_results(base: ExecutionResult, other: ExecutionResult) -> ExecutionResult:
    base.accepted = base.accepted and other.accepted
    base.orders.extend(other.orders)
    base.fills.extend(other.fills)
    if other.position is not None:
        base.position = other.position
    base.positions.extend(other.positions)
    if other.account_state is not None:
        base.account_state = other.account_state
    if other.pnl_snapshot is not None:
        base.pnl_snapshot = other.pnl_snapshot
    if other.reason is not None:
        base.reason = other.reason
    if other.payload:
        base.payload.update(other.payload)
    return base


class ExecutionEngine:
    def __init__(self, *, config: AppSettings, venue: ExecutionVenue) -> None:
        self.config = config
        self.venue = venue
        self.bracket_manager = BracketManager(config=config, venue=venue)

    async def submit(self, plan: ExecutionPlan) -> ExecutionResult:
        aggregate = ExecutionResult(accepted=True)
        if plan.metadata.get("cancel_active_bracket"):
            cancelled = await self.bracket_manager.cancel_for_symbol(plan.entry_order.symbol, as_of=plan.entry_order.submitted_at)
            _merge_results(aggregate, cancelled)

        submitted = await self.venue.submit(plan)
        _merge_results(aggregate, submitted)
        self.bracket_manager.register_plan(plan=plan, result=submitted)
        return aggregate

    async def on_market_event(self, *, symbol: str, snapshot: MarketSnapshot, as_of: datetime) -> ExecutionResult:
        result = await self.venue.process_market_event(symbol, snapshot, as_of)
        bracket_follow_up = await self.bracket_manager.on_execution_result(result, as_of=as_of)
        return _merge_results(result, bracket_follow_up)

    async def cancel_order(self, order_id: str, *, as_of: datetime) -> ExecutionResult:
        return await self.venue.cancel_order(order_id, as_of=as_of)

    async def snapshot_state(self) -> VenueStateSnapshot:
        return await self.venue.snapshot_state()

    async def rearm_bracket(self, symbol: str, *, as_of: datetime) -> ExecutionResult:
        return await self.bracket_manager.rearm_bracket(symbol, as_of=as_of)

    async def emergency_flatten(self, symbol: str, *, as_of: datetime, reason: str) -> ExecutionResult:
        return await self.bracket_manager.emergency_flatten(symbol, as_of=as_of, reason=reason)

    def active_brackets(self) -> dict[str, BracketState]:
        return self.bracket_manager.active_brackets()

    async def sync_positions(self) -> list[PositionState]:
        return await self.venue.sync_positions()

    def account_state(self) -> AccountState:
        return self.venue.account_state()
