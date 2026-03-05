from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trading_bot.config.schema import AppSettings
from trading_bot.domain.enums import ExecutionVenueKind, RiskDecisionType, TradeAction
from trading_bot.domain.models import AccountState, Instrument, MarketSnapshot, RuntimeState, TradeIntent
from trading_bot.marketdata.events import OrderBookEvent, OrderBookLevel
from trading_bot.risk.basic import BasicRiskEngine


def _build_settings(*, execution_enabled: bool = True, symbol_allowlist: list[str] | None = None) -> AppSettings:
    return AppSettings.model_validate(
        {
            "runtime": {"service_name": "trading-bot", "mode": "live", "environment": "test"},
            "exchange": {
                "primary": "bybit",
                "market_type": "linear_perp",
                "position_mode": "one_way",
                "account_alias": "default",
                "testnet": True,
                "private_state_enabled": True,
            },
            "symbols": {"allowlist": ["BTCUSDT"]},
            "live": {
                "execution_enabled": execution_enabled,
                "allow_mainnet": False,
                "symbol_allowlist": symbol_allowlist if symbol_allowlist is not None else ["BTCUSDT"],
                "max_order_notional_usdt": "100",
                "max_position_notional_usdt": "100",
                "max_total_exposure_usdt": "100",
                "private_state_stale_after_seconds": 10,
            },
            "storage": {
                "postgres_dsn": "postgresql+asyncpg://user:pass@localhost:5432/app",
                "redis_dsn": "redis://localhost:6379/0",
            },
            "observability": {"log_level": "INFO", "http_host": "127.0.0.1", "http_port": 8080},
            "risk": {"max_open_positions": 2, "risk_per_trade": 0.01, "max_daily_loss": 0.1},
            "llm": {"enabled": False, "provider": "none", "model_name": "", "timeout_seconds": 10},
        }
    )


def _build_snapshot() -> MarketSnapshot:
    now = datetime.now(UTC)
    return MarketSnapshot(
        symbol="BTCUSDT",
        instrument=Instrument(
            exchange_name="bybit",
            symbol="BTCUSDT",
            market_type="linear_perp",
            tick_size=Decimal("0.1"),
            lot_size=Decimal("0.001"),
            min_quantity=Decimal("0.001"),
            min_notional=Decimal("5"),
            quote_asset="USDT",
            base_asset="BTC",
        ),
        orderbook=OrderBookEvent(
            exchange_name="bybit",
            symbol="BTCUSDT",
            event_ts=now,
            depth=50,
            bids=[OrderBookLevel(price=Decimal("9999"), size=Decimal("1"))],
            asks=[OrderBookLevel(price=Decimal("10000"), size=Decimal("1"))],
        ),
    )


def _build_state() -> RuntimeState:
    now = datetime.now(UTC)
    state = RuntimeState(
        run_session_id="run-1",
        run_mode="live",
        execution_venue=ExecutionVenueKind.LIVE,
    )
    state.account_state = AccountState(
        exchange_name="bybit",
        execution_venue=ExecutionVenueKind.LIVE,
        equity=Decimal("1000"),
        available_balance=Decimal("1000"),
        updated_at=now,
    )
    state.venue_connectivity_state.last_private_event_at = now
    return state


def _build_open_intent() -> TradeIntent:
    return TradeIntent(
        strategy_name="test",
        action=TradeAction.OPEN_LONG,
        symbol="BTCUSDT",
        side="buy",
        reference_price=Decimal("10000"),
        stop_loss_price=Decimal("9900"),
        take_profit_price=Decimal("10200"),
    )


@pytest.mark.asyncio
async def test_live_guard_rejects_when_execution_disabled() -> None:
    settings = _build_settings(execution_enabled=False)
    engine = BasicRiskEngine(config=settings)

    decision = await engine.assess(_build_open_intent(), _build_state(), _build_snapshot())

    assert decision.decision == RiskDecisionType.REJECT
    assert decision.reasons == ["live_execution_disabled"]


@pytest.mark.asyncio
async def test_live_guard_rejects_symbol_not_allowed() -> None:
    settings = _build_settings(symbol_allowlist=["ETHUSDT"])
    engine = BasicRiskEngine(config=settings)

    decision = await engine.assess(_build_open_intent(), _build_state(), _build_snapshot())

    assert decision.decision == RiskDecisionType.REJECT
    assert decision.reasons == ["live_symbol_not_allowed"]


@pytest.mark.asyncio
async def test_live_guard_rejects_stale_private_state() -> None:
    settings = _build_settings()
    engine = BasicRiskEngine(config=settings)
    state = _build_state()
    state.venue_connectivity_state.last_private_event_at = datetime.now(UTC) - timedelta(seconds=60)

    decision = await engine.assess(_build_open_intent(), state, _build_snapshot())

    assert decision.decision == RiskDecisionType.REJECT
    assert decision.reasons == ["live_private_state_stale"]


@pytest.mark.asyncio
async def test_live_guard_rejects_order_notional_limit() -> None:
    settings = _build_settings()
    settings.live.max_order_notional_usdt = Decimal("1")
    engine = BasicRiskEngine(config=settings)

    decision = await engine.assess(_build_open_intent(), _build_state(), _build_snapshot())

    assert decision.decision == RiskDecisionType.REJECT
    assert decision.reasons == ["live_order_notional_limit"]
