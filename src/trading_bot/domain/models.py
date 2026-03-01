from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from trading_bot.domain.enums import Environment, ExchangeName, MarketType, PositionMode, RunMode, ServiceStatus


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Instrument(DomainModel):
    exchange_name: ExchangeName
    symbol: str
    market_type: MarketType
    tick_size: Decimal
    lot_size: Decimal
    min_quantity: Decimal
    quote_asset: str
    base_asset: str


class AccountState(DomainModel):
    exchange_name: ExchangeName
    equity: Decimal
    available_balance: Decimal
    margin_mode: str = "isolated"
    position_mode: PositionMode = PositionMode.ONE_WAY


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
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class FillState(DomainModel):
    order_id: str
    price: Decimal
    quantity: Decimal
    fee: Decimal = Decimal("0")
    liquidity_type: str = "unknown"
    filled_at: datetime = Field(default_factory=datetime.utcnow)


class PositionState(DomainModel):
    exchange_name: ExchangeName
    symbol: str
    side: str
    quantity: Decimal
    entry_price: Decimal
    leverage: Decimal = Decimal("1")
    realized_pnl: Decimal = Decimal("0")
    status: str = "open"
    opened_at: datetime = Field(default_factory=datetime.utcnow)
    closed_at: datetime | None = None


class SignalEvent(DomainModel):
    run_mode: RunMode
    symbol: str
    strategy_name: str
    signal_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class TradeIntent(DomainModel):
    symbol: str
    side: str
    confidence: float = Field(ge=0, le=1)
    payload: dict[str, Any] = Field(default_factory=dict)


class RiskDecision(DomainModel):
    decision: str
    reasons: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


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
