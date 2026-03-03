from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def generate_id() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class RunSessionRecord(Base):
    __tablename__ = "run_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    run_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    environment: Mapped[str] = mapped_column(String(16), nullable=False)
    execution_venue: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created")
    summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InstrumentRecord(Base):
    __tablename__ = "instruments"
    __table_args__ = (
        UniqueConstraint("exchange_name", "symbol", name="uq_instruments_exchange_symbol"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    exchange_name: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    market_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="trading")
    tick_size: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    lot_size: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    min_quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    min_notional: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    max_order_quantity: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    max_leverage: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    price_scale: Mapped[int | None] = mapped_column(nullable=True)
    quote_asset: Mapped[str] = mapped_column(String(16), nullable=False)
    base_asset: Mapped[str] = mapped_column(String(16), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class ConfigSnapshotRecord(Base):
    __tablename__ = "config_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    run_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class SignalEventRecord(Base):
    __tablename__ = "signal_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    run_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy_name: Mapped[str] = mapped_column(String(128), nullable=False)
    signal_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class RiskDecisionRecord(Base):
    __tablename__ = "risk_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    signal_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    run_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    intent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    reasons_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class OrderRecord(Base):
    __tablename__ = "orders"
    __table_args__ = (Index("ix_orders_exchange_order_id", "exchange_name", "exchange_order_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    run_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    exchange_name: Mapped[str] = mapped_column(String(32), nullable=False)
    execution_venue: Mapped[str] = mapped_column(String(16), nullable=False, default="live")
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    order_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    stop_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    reduce_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    exchange_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    client_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    intent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    filled_quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, default=Decimal("0"))
    average_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    time_in_force: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )


class FillRecord(Base):
    __tablename__ = "fills"
    __table_args__ = (
        Index("ix_fills_exchange_fill_id", "exchange_name", "exchange_fill_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    run_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    order_id: Mapped[str] = mapped_column(String(36), nullable=False)
    exchange_name: Mapped[str] = mapped_column(String(32), nullable=False, default="bybit")
    execution_venue: Mapped[str] = mapped_column(String(16), nullable=False, default="live")
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    side: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    price: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    fee: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, default=Decimal("0"))
    fee_asset: Mapped[str] = mapped_column(String(16), nullable=False, default="USDT")
    liquidity_type: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    is_maker: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    slippage_bps: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, default=Decimal("0"))
    exchange_fill_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class PositionRecord(Base):
    __tablename__ = "positions"
    __table_args__ = (Index("ix_positions_exchange_symbol_status", "exchange_name", "symbol", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    run_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    exchange_name: Mapped[str] = mapped_column(String(32), nullable=False)
    execution_venue: Mapped[str] = mapped_column(String(16), nullable=False, default="live")
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    mark_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    last_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    leverage: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False, default=Decimal("1"))
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, default=Decimal("0"))
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, default=Decimal("0"))
    fees_paid: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, default=Decimal("0"))
    closed_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class AccountSnapshotRecord(Base):
    __tablename__ = "account_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    run_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    exchange_name: Mapped[str] = mapped_column(String(32), nullable=False)
    execution_venue: Mapped[str] = mapped_column(String(16), nullable=False, default="live")
    account_type: Mapped[str] = mapped_column(String(32), nullable=False, default="UNIFIED")
    equity: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    available_balance: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    wallet_balance: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    margin_balance: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, default=Decimal("0"))
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, default=Decimal("0"))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class LLMAdviceRecord(Base):
    __tablename__ = "llm_advice"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    run_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    advice_type: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class PnlSnapshotRecord(Base):
    __tablename__ = "pnl_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    run_session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    execution_venue: Mapped[str] = mapped_column(String(16), nullable=False)
    event_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    equity: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    balance: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    drawdown: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
