from __future__ import annotations

from datetime import datetime
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
    RuntimeState,
    TradeIntent,
    VenueStateSnapshot,
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
    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def submit(self, plan: ExecutionPlan) -> ExecutionResult: ...

    async def process_market_event(self, symbol: str, snapshot: MarketSnapshot, as_of: datetime) -> ExecutionResult: ...

    async def drain_pending_updates(self, *, as_of: datetime) -> ExecutionResult: ...

    async def cancel_order(self, order_id: str, *, as_of: datetime) -> ExecutionResult: ...

    async def snapshot_state(self) -> VenueStateSnapshot: ...

    async def sync_positions(self) -> list[PositionState]: ...

    def account_state(self) -> AccountState: ...


@runtime_checkable
class Strategy(Protocol):
    async def evaluate(self, snapshot: MarketSnapshot, features: FeatureSnapshot) -> list[TradeIntent]: ...


@runtime_checkable
class RiskEngine(Protocol):
    async def assess(self, intent: TradeIntent, state: RuntimeState, snapshot: MarketSnapshot) -> RiskDecision: ...


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


@runtime_checkable
class MarketEventFeed(Protocol):
    async def fetch_instruments(self, symbols: Sequence[str]) -> list[Instrument]: ...

    async def prime(self, symbols: Sequence[str]) -> list[MarketEvent]: ...

    async def stream(self, symbols: Sequence[str]) -> AsyncIterator[MarketEvent]: ...

    async def close(self) -> None: ...


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...

    async def sleep_until(self, dt: datetime) -> None: ...
