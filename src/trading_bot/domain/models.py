from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal
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
        LiquidationEvent,
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
    stop_price: Decimal | None = None
    reduce_only: bool = False
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
    quantity: Decimal | None = None
    reference_price: Decimal
    limit_price: Decimal | None = None
    stop_loss_price: Decimal | None = None
    take_profit_price: Decimal | None = None
    ttl_ms: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    generated_at: datetime = Field(default_factory=utc_now)


class ExecutionPlan(DomainModel):
    execution_venue: ExecutionVenueKind
    entry_order: OrderIntent
    protective_orders: list[OrderIntent] = Field(default_factory=list)
    intent_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class RiskDecision(DomainModel):
    decision: RiskDecisionType
    reasons: list[str] = Field(default_factory=list)
    execution_plan: ExecutionPlan | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class AdvisorOutput(DomainModel):
    action: Literal["long", "short", "no_trade"] = "no_trade"
    confidence_pct: float = Field(default=0.0, ge=0, le=100)
    market_regime: str = "unknown"
    smart_money_signal: str = "neutral"
    trade_idea: dict[str, Any] | None = None
    evidence: list[str] = Field(default_factory=list)
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


class BiasState(DomainModel):
    timeframe: str = "15m"
    state: str = "neutral"
    event_type: str | None = None
    direction: str = "neutral"
    age_bars: int | None = None
    last_event_at: datetime | None = None


class StructureState(DomainModel):
    timeframe: str
    direction: str = "neutral"
    event_type: str | None = None
    pivot_high: Decimal | None = None
    pivot_low: Decimal | None = None
    break_price: Decimal | None = None
    age_bars: int | None = None
    last_event_at: datetime | None = None
    is_active: bool = False


class LiquiditySweepState(DomainModel):
    side: str
    swept_level: Decimal
    sweep_at: datetime
    reclaim_at: datetime
    age_bars: int
    is_active: bool = True


class FairValueGapZone(DomainModel):
    side: str
    lower_bound: Decimal
    upper_bound: Decimal
    created_at: datetime
    timeframe: str = "1m"
    age_bars: int
    is_active: bool = True
    touched: bool = False


class OrderBlockZone(DomainModel):
    side: str
    lower_bound: Decimal
    upper_bound: Decimal
    created_at: datetime
    timeframe: str = "1m"
    age_bars: int
    source_event_type: str | None = None
    is_active: bool = True
    touched: bool = False


class OrderBookFeatureState(DomainModel):
    imbalance_levels: int = 5
    imbalance: float = 0.0
    has_fresh_orderbook: bool = False
    supportive_long_imbalance: bool = False
    supportive_short_imbalance: bool = False
    has_bid_wall: bool = False
    has_ask_wall: bool = False
    bid_wall_price: Decimal | None = None
    ask_wall_price: Decimal | None = None
    bid_wall_size: Decimal | None = None
    ask_wall_size: Decimal | None = None
    wall_persistence: int = 0


class OpenInterestFeatureState(DomainModel):
    available: bool = False
    delta_bps: Decimal = Decimal("0")
    supportive_long: bool = False
    supportive_short: bool = False
    lookback_points: int = 0


class FundingFeatureState(DomainModel):
    enabled: bool = True
    available: bool = False
    funding_rate: Decimal = Decimal("0")
    blocks_long: bool = False
    blocks_short: bool = False


class LiquidationFeatureState(DomainModel):
    enabled: bool = False
    available: bool = False
    supportive_long: bool = False
    supportive_short: bool = False
    same_side_events: int = 0
    window_seconds: int = 0


class SetupCandidate(DomainModel):
    side: str
    zone_type: str
    lower_bound: Decimal
    upper_bound: Decimal
    created_at: datetime
    age_bars: int
    touched: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class FeatureSnapshot(DomainModel):
    symbol: str
    last_close_change_bps: Decimal = Decimal("0")
    top5_imbalance: float = 0.0
    open_interest_delta: Decimal = Decimal("0")
    funding_rate: Decimal = Decimal("0")
    has_fresh_orderbook: bool = False
    bias_state: BiasState = Field(default_factory=BiasState)
    structure_state: StructureState = Field(default_factory=lambda: StructureState(timeframe="5m"))
    entry_structure_state: StructureState = Field(default_factory=lambda: StructureState(timeframe="1m"))
    sweep: LiquiditySweepState | None = None
    active_fvgs: list[FairValueGapZone] = Field(default_factory=list)
    active_order_blocks: list[OrderBlockZone] = Field(default_factory=list)
    orderbook_state: OrderBookFeatureState = Field(default_factory=OrderBookFeatureState)
    open_interest_state: OpenInterestFeatureState = Field(default_factory=OpenInterestFeatureState)
    funding_state: FundingFeatureState = Field(default_factory=FundingFeatureState)
    liquidation_state: LiquidationFeatureState = Field(default_factory=LiquidationFeatureState)
    setup_candidates: list[SetupCandidate] = Field(default_factory=list)
    warmup_complete: bool = False
    payload: dict[str, Any] = Field(default_factory=dict)


class KillSwitchState(DomainModel):
    daily_loss_breached_until: datetime | None = None
    consecutive_loss_cooldown_until: datetime | None = None
    protection_failure_active: bool = False
    protection_failure_reason: str | None = None
    last_reason: str | None = None


class LossStreakState(DomainModel):
    consecutive_losses: int = 0
    last_closed_trade_pnl: Decimal | None = None
    cooldown_until: datetime | None = None


class BracketState(DomainModel):
    symbol: str
    intent_id: str
    side: str
    quantity: Decimal
    stop_loss_price: Decimal
    take_profit_price: Decimal
    entry_order_id: str | None = None
    stop_loss_order_id: str | None = None
    take_profit_order_id: str | None = None
    status: str = "pending_entry"
    last_error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class VenueConnectivityState(DomainModel):
    private_ws_connected: bool = False
    last_private_event_at: datetime | None = None
    last_successful_rest_sync_at: datetime | None = None
    stale_reason: str | None = None


class VenueStateSnapshot(DomainModel):
    account_state: AccountState | None = None
    open_orders: list[OrderState] = Field(default_factory=list)
    open_positions: list[PositionState] = Field(default_factory=list)
    connectivity_state: VenueConnectivityState = Field(default_factory=VenueConnectivityState)
    as_of: datetime = Field(default_factory=utc_now)


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
    positions: list[PositionState] = Field(default_factory=list)
    account_state: AccountState | None = None
    pnl_snapshot: PnlSnapshot | None = None
    reason: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class RuntimeState(DomainModel):
    run_session_id: str
    run_mode: RunMode
    execution_venue: ExecutionVenueKind
    account_state: AccountState | None = None
    venue_connectivity_state: VenueConnectivityState = Field(default_factory=VenueConnectivityState)
    open_orders: dict[str, OrderState] = Field(default_factory=dict)
    open_positions: dict[str, PositionState] = Field(default_factory=dict)
    market_state_by_symbol: dict[str, MarketSnapshot] = Field(default_factory=dict)
    kill_switch_state: KillSwitchState = Field(default_factory=KillSwitchState)
    loss_streak_state: LossStreakState = Field(default_factory=LossStreakState)
    active_brackets_by_symbol: dict[str, BracketState] = Field(default_factory=dict)
    day_start_equity_by_utc_date: dict[str, Decimal] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=utc_now)


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
