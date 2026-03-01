from __future__ import annotations

from typing import Protocol, runtime_checkable

from trading_bot.domain.models import (
    AccountState,
    AdvisorOutput,
    ExecutionPlan,
    ExecutionResult,
    FeatureSnapshot,
    Instrument,
    MarketSnapshot,
    OrderIntent,
    OrderState,
    PositionState,
    RiskDecision,
    TradeIntent,
)


@runtime_checkable
class ExchangeAdapter(Protocol):
    async def connect(self) -> None: ...

    async def fetch_instruments(self) -> list[Instrument]: ...

    async def fetch_account_state(self) -> AccountState: ...

    async def place_order(self, intent: OrderIntent) -> OrderState: ...

    async def cancel_order(self, order_id: str) -> None: ...

    async def list_open_positions(self) -> list[PositionState]: ...

    async def close(self) -> None: ...


@runtime_checkable
class ExecutionVenue(Protocol):
    async def submit(self, plan: ExecutionPlan) -> ExecutionResult: ...

    async def sync_positions(self) -> list[PositionState]: ...


@runtime_checkable
class Strategy(Protocol):
    async def evaluate(self, snapshot: MarketSnapshot, features: FeatureSnapshot) -> list[TradeIntent]: ...


@runtime_checkable
class RiskEngine(Protocol):
    async def assess(self, intent: TradeIntent, account_state: AccountState) -> RiskDecision: ...


@runtime_checkable
class LLMAdvisor(Protocol):
    async def advise(self, snapshot: MarketSnapshot, features: FeatureSnapshot) -> AdvisorOutput: ...
