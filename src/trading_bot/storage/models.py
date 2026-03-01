from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, Numeric, String
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
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    reasons_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class OrderRecord(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    run_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    exchange_name: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    order_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    stop_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    reduce_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    exchange_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )


class FillRecord(Base):
    __tablename__ = "fills"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    order_id: Mapped[str] = mapped_column(String(36), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    fee: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, default=Decimal("0"))
    liquidity_type: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class PositionRecord(Base):
    __tablename__ = "positions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    run_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    exchange_name: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    leverage: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False, default=Decimal("1"))
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, default=Decimal("0"))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
