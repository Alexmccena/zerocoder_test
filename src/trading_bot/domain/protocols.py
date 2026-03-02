from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Protocol, runtime_checkable

from trading_bot.domain.models import (
    AccountState,
    AdvisorOutput,
    ExchangeCapabilities,
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
from trading_bot.marketdata.events import MarketEvent, PrivateStateEvent


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


@runtime_checkable
class MarketDataSource(Protocol):
    async def fetch_instruments(self, symbols: Sequence[str] | None = None) -> list[Instrument]: ...

    async def stream_public_events(self, symbols: Sequence[str]) -> AsyncIterator[MarketEvent]: ...

    async def fetch_open_interest(self, symbol: str) -> MarketEvent | None: ...

    async def fetch_funding_rate(self, symbol: str) -> MarketEvent | None: ...

    def describe_capabilities(self) -> ExchangeCapabilities: ...


@runtime_checkable
class PrivateStateSource(Protocol):
    async def fetch_account_state(self) -> AccountState: ...

    async def list_open_orders(self, symbol: str | None = None) -> list[OrderState]: ...

    async def list_open_positions(self) -> list[PositionState]: ...

    async def stream_private_events(self) -> AsyncIterator[PrivateStateEvent]: ...
