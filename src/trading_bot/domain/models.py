from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from trading_bot.domain.enums import (
    EntryType,
    Environment,
    ExchangeName,
    ExecutionVenueKind,
    MarketType,
    PositionMode,
    RiskDecisionType,
    RunMode,
    ServiceStatus,
    TradeAction,
)

if TYPE_CHECKING:
    from trading_bot.marketdata.events import (
        FundingRateEvent,
        KlineEvent,
        OpenInterestEvent,
        OrderBookEvent,
        TickerEvent,
        TradeEvent,
    )


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Instrument(DomainModel):
    exchange_name: ExchangeName
    symbol: str
    market_type: MarketType
    tick_size: Decimal
    lot_size: Decimal
    min_quantity: Decimal
    quote_asset: str
    base_asset: str
    status: str = "trading"
    price_scale: int | None = None
    min_notional: Decimal | None = None
    max_order_quantity: Decimal | None = None
    max_leverage: Decimal | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=utc_now)


class AccountState(DomainModel):
    exchange_name: ExchangeName
    execution_venue: ExecutionVenueKind = ExecutionVenueKind.LIVE
    equity: Decimal
    available_balance: Decimal
    wallet_balance: Decimal = Decimal("0")
    margin_balance: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    account_type: str = "UNIFIED"
    margin_mode: str = "isolated"
    position_mode: PositionMode = PositionMode.ONE_WAY
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=utc_now)


class OrderIntent(DomainModel):
    client_order_id: str = Field(default_factory=lambda: str(uuid4()))
    intent_id: str | None = None
    exchange_name: ExchangeName
    execution_venue: ExecutionVenueKind = ExecutionVenueKind.LIVE
    symbol: str
    side: str
    order_type: str
    quantity: Decimal
    price: Decimal | None = None
    stop_price: Decimal | None = None
    reduce_only: bool = False
    ttl_ms: int | None = None
    submitted_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OrderState(DomainModel):
    order_id: str
    exchange_name: ExchangeName
    execution_venue: ExecutionVenueKind = ExecutionVenueKind.LIVE
    symbol: str
    side: str
    order_type: str
    status: str
    quantity: Decimal
    price: Decimal | None = None
    filled_quantity: Decimal = Decimal("0")
    average_price: Decimal | None = None
    exchange_order_id: str | None = None
    client_order_id: str | None = None
    intent_id: str | None = None
    time_in_force: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    submitted_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime | None = None
    cancel_reason: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class FillState(DomainModel):
    order_id: str
    run_session_id: str | None = None
    exchange_name: ExchangeName = ExchangeName.BYBIT
    execution_venue: ExecutionVenueKind = ExecutionVenueKind.LIVE
    symbol: str = ""
    side: str = ""
    price: Decimal
    quantity: Decimal
    fee: Decimal = Decimal("0")
    fee_asset: str = "USDT"
    liquidity_type: str = "unknown"
    is_maker: bool = False
    slippage_bps: Decimal = Decimal("0")
    exchange_fill_id: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    filled_at: datetime = Field(default_factory=utc_now)


class PositionState(DomainModel):
    exchange_name: ExchangeName
    execution_venue: ExecutionVenueKind = ExecutionVenueKind.LIVE
    symbol: str
    side: str
    quantity: Decimal
    entry_price: Decimal
    mark_price: Decimal | None = None
    last_price: Decimal | None = None
    leverage: Decimal = Decimal("1")
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    fees_paid: Decimal = Decimal("0")
    status: str = "open"
    closed_reason: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    opened_at: datetime = Field(default_factory=utc_now)
    closed_at: datetime | None = None
    updated_at: datetime = Field(default_factory=utc_now)


class SignalEvent(DomainModel):
    run_mode: RunMode
    symbol: str
    strategy_name: str
    signal_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class TradeIntent(DomainModel):
    intent_id: str = Field(default_factory=lambda: str(uuid4()))
    strategy_name: str
    action: TradeAction
    symbol: str
    side: str
    entry_type: EntryType = EntryType.MARKET
    quantity: Decimal
    reference_price: Decimal
    limit_price: Decimal | None = None
    ttl_ms: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    generated_at: datetime = Field(default_factory=utc_now)


class RiskDecision(DomainModel):
    decision: RiskDecisionType
    reasons: list[str] = Field(default_factory=list)
    execution_plan: ExecutionPlan | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class AdvisorOutput(DomainModel):
    market_bias: str = "neutral"
    setup_quality_score: float = Field(default=0.0, ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)
    narrative: str = ""
    recommended_focus_symbols: list[str] = Field(default_factory=list)


class MarketSnapshot(DomainModel):
    symbol: str
    as_of: datetime = Field(default_factory=utc_now)
    instrument: Instrument | None = None
    orderbook: OrderBookEvent | None = None
    ticker: TickerEvent | None = None
    last_trade: TradeEvent | None = None
    closed_klines_by_interval: dict[str, KlineEvent] = Field(default_factory=dict)
    open_interest: OpenInterestEvent | None = None
    funding_rate: FundingRateEvent | None = None
    data_is_stale: bool = False


class FeatureSnapshot(DomainModel):
    symbol: str
    last_close_change_bps: Decimal = Decimal("0")
    top5_imbalance: float = 0.0
    open_interest_delta: Decimal = Decimal("0")
    funding_rate: Decimal = Decimal("0")
    has_fresh_orderbook: bool = False
    payload: dict[str, Any] = Field(default_factory=dict)


class ExecutionPlan(DomainModel):
    execution_venue: ExecutionVenueKind
    entry_order: OrderIntent
    protective_orders: list[OrderIntent] = Field(default_factory=list)
    intent_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class PnlSnapshot(DomainModel):
    run_session_id: str | None = None
    execution_venue: ExecutionVenueKind = ExecutionVenueKind.PAPER
    event_ts: datetime = Field(default_factory=utc_now)
    equity: Decimal
    balance: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    drawdown: Decimal
    payload: dict[str, Any] = Field(default_factory=dict)


class ExecutionResult(DomainModel):
    accepted: bool
    orders: list[OrderState] = Field(default_factory=list)
    fills: list[FillState] = Field(default_factory=list)
    position: PositionState | None = None
    account_state: AccountState | None = None
    pnl_snapshot: PnlSnapshot | None = None
    reason: str | None = None


class RuntimeState(DomainModel):
    run_session_id: str
    run_mode: RunMode
    execution_venue: ExecutionVenueKind
    account_state: AccountState | None = None
    open_orders: dict[str, OrderState] = Field(default_factory=dict)
    open_positions: dict[str, PositionState] = Field(default_factory=dict)
    market_state_by_symbol: dict[str, MarketSnapshot] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=utc_now)


from trading_bot.marketdata.events import FundingRateEvent, KlineEvent, OpenInterestEvent, OrderBookEvent, TickerEvent, TradeEvent

MarketSnapshot.model_rebuild()


class HealthReport(DomainModel):
    status: ServiceStatus
    service: str
    environment: Environment
    checks: dict[str, ServiceStatus]


class ExchangeCapabilities(DomainModel):
    exchange_name: ExchangeName
    channels: dict[str, bool] = Field(default_factory=dict)
    rest_features: dict[str, bool] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=utc_now)
