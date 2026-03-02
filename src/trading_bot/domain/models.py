from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from trading_bot.domain.enums import Environment, ExchangeName, MarketType, PositionMode, RunMode, ServiceStatus


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
    exchange_name: ExchangeName
    symbol: str
    side: str
    order_type: str
    quantity: Decimal
    price: Decimal | None = None
    stop_price: Decimal | None = None
    reduce_only: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class OrderState(DomainModel):
    order_id: str
    exchange_name: ExchangeName
    symbol: str
    side: str
    order_type: str
    status: str
    quantity: Decimal
    filled_quantity: Decimal = Decimal("0")
    average_price: Decimal | None = None
    exchange_order_id: str | None = None
    time_in_force: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class FillState(DomainModel):
    order_id: str
    exchange_name: ExchangeName = ExchangeName.BYBIT
    symbol: str = ""
    side: str = ""
    price: Decimal
    quantity: Decimal
    fee: Decimal = Decimal("0")
    liquidity_type: str = "unknown"
    exchange_fill_id: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    filled_at: datetime = Field(default_factory=utc_now)


class PositionState(DomainModel):
    exchange_name: ExchangeName
    symbol: str
    side: str
    quantity: Decimal
    entry_price: Decimal
    mark_price: Decimal | None = None
    leverage: Decimal = Decimal("1")
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    status: str = "open"
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
    symbol: str
    side: str
    confidence: float = Field(ge=0, le=1)
    payload: dict[str, Any] = Field(default_factory=dict)


class RiskDecision(DomainModel):
    decision: str
    reasons: list[str] = Field(default_factory=list)
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
    payload: dict[str, Any] = Field(default_factory=dict)


class FeatureSnapshot(DomainModel):
    symbol: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ExecutionPlan(DomainModel):
    intent: OrderIntent
    protective_orders: list[OrderIntent] = Field(default_factory=list)


class ExecutionResult(DomainModel):
    accepted: bool
    orders: list[OrderState] = Field(default_factory=list)
    reason: str | None = None


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
