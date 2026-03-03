from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading_bot.config.schema import AppSettings
from trading_bot.domain.enums import EntryType, ExchangeName, ExecutionVenueKind, MarketType, PositionMode, TradeAction
from trading_bot.domain.models import AccountState, Instrument, MarketSnapshot, PositionState, RuntimeState, TradeIntent
from trading_bot.marketdata.events import FundingRateEvent, OrderBookEvent, OrderBookLevel
from trading_bot.risk.basic import BasicRiskEngine


def _build_settings() -> AppSettings:
    return AppSettings.model_validate(
        {
            "runtime": {"service_name": "tb", "mode": "paper", "environment": "dev"},
            "exchange": {
                "primary": "bybit",
                "market_type": "linear_perp",
                "position_mode": "one_way",
                "account_alias": "default",
                "testnet": True,
            },
            "symbols": {"allowlist": ["BTCUSDT"]},
            "storage": {"postgres_dsn": "postgresql+asyncpg://u:p@localhost/db", "redis_dsn": "redis://localhost:6379/0"},
            "observability": {"log_level": "INFO", "http_host": "127.0.0.1", "http_port": 8080},
            "risk": {
                "max_open_positions": 2,
                "risk_per_trade": 0.01,
                "max_daily_loss": 0.2,
                "leverage_cap": "1",
                "funding_blackout_minutes_before": 5,
                "funding_blackout_minutes_after": 5,
            },
            "llm": {"enabled": False, "provider": "none", "model_name": "", "timeout_seconds": 10},
        }
    )


def _build_state(*, equity: str = "100", available_balance: str = "100") -> RuntimeState:
    now = datetime.now(timezone.utc)
    return RuntimeState(
        run_session_id="run-1",
        run_mode="paper",
        execution_venue=ExecutionVenueKind.PAPER,
        account_state=AccountState(
            exchange_name=ExchangeName.BYBIT,
            execution_venue=ExecutionVenueKind.PAPER,
            equity=Decimal(equity),
            available_balance=Decimal(available_balance),
            wallet_balance=Decimal(available_balance),
            margin_balance=Decimal(available_balance),
            position_mode=PositionMode.ONE_WAY,
            updated_at=now,
        ),
    )


def _build_snapshot(*, funding_next_at: datetime | None = None) -> MarketSnapshot:
    now = datetime.now(timezone.utc)
    return MarketSnapshot(
        symbol="BTCUSDT",
        as_of=now,
        instrument=Instrument(
            exchange_name=ExchangeName.BYBIT,
            symbol="BTCUSDT",
            market_type=MarketType.LINEAR_PERP,
            tick_size=Decimal("0.1"),
            lot_size=Decimal("0.001"),
            min_quantity=Decimal("0.001"),
            quote_asset="USDT",
            base_asset="BTC",
        ),
        orderbook=OrderBookEvent(
            exchange_name=ExchangeName.BYBIT,
            symbol="BTCUSDT",
            event_ts=now,
            depth=50,
            bids=[OrderBookLevel(price=Decimal("99.9"), size=Decimal("10"))],
            asks=[OrderBookLevel(price=Decimal("100.0"), size=Decimal("10"))],
        ),
        funding_rate=(
            FundingRateEvent(
                exchange_name=ExchangeName.BYBIT,
                symbol="BTCUSDT",
                event_ts=now,
                funding_rate=Decimal("0.0001"),
                next_funding_at=funding_next_at,
            )
            if funding_next_at is not None
            else None
        ),
    )


def test_risk_engine_sizes_open_intent_and_builds_protective_orders() -> None:
    settings = _build_settings()
    engine = BasicRiskEngine(config=settings)
    state = _build_state()
    snapshot = _build_snapshot()
    intent = TradeIntent(
        strategy_name="phase3_placeholder",
        action=TradeAction.OPEN_LONG,
        symbol="BTCUSDT",
        side="buy",
        entry_type=EntryType.MARKET,
        reference_price=Decimal("100"),
        stop_loss_price=Decimal("99"),
        take_profit_price=Decimal("102"),
        generated_at=snapshot.as_of,
    )

    decision = __import__("asyncio").run(engine.assess(intent, state, snapshot))

    assert decision.decision.value == "allow"
    assert decision.execution_plan is not None
    assert decision.execution_plan.entry_order.quantity == Decimal("1.000")
    assert [order.metadata["order_role"] for order in decision.execution_plan.protective_orders] == [
        "stop_loss",
        "take_profit",
    ]


def test_risk_engine_rejects_invalid_protective_geometry() -> None:
    settings = _build_settings()
    engine = BasicRiskEngine(config=settings)
    state = _build_state()
    snapshot = _build_snapshot()
    intent = TradeIntent(
        strategy_name="phase3_placeholder",
        action=TradeAction.OPEN_LONG,
        symbol="BTCUSDT",
        side="buy",
        entry_type=EntryType.MARKET,
        reference_price=Decimal("100"),
        stop_loss_price=Decimal("100"),
        take_profit_price=Decimal("101"),
        generated_at=snapshot.as_of,
    )

    decision = __import__("asyncio").run(engine.assess(intent, state, snapshot))

    assert decision.decision.value == "reject"
    assert decision.reasons == ["invalid_protective_geometry"]


def test_risk_engine_blocks_open_during_funding_blackout_but_allows_close() -> None:
    settings = _build_settings()
    engine = BasicRiskEngine(config=settings)
    state = _build_state()
    now = datetime.now(timezone.utc)
    snapshot = _build_snapshot(funding_next_at=now + timedelta(minutes=2))

    open_intent = TradeIntent(
        strategy_name="phase3_placeholder",
        action=TradeAction.OPEN_LONG,
        symbol="BTCUSDT",
        side="buy",
        entry_type=EntryType.MARKET,
        reference_price=Decimal("100"),
        stop_loss_price=Decimal("99"),
        take_profit_price=Decimal("102"),
        generated_at=now,
    )
    open_decision = __import__("asyncio").run(engine.assess(open_intent, state, snapshot))

    assert open_decision.decision.value == "reject"
    assert open_decision.reasons == ["funding_blackout_active"]

    state.open_positions["BTCUSDT"] = PositionState(
        exchange_name=ExchangeName.BYBIT,
        execution_venue=ExecutionVenueKind.PAPER,
        symbol="BTCUSDT",
        side="long",
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
    )
    close_intent = TradeIntent(
        strategy_name="phase3_placeholder",
        action=TradeAction.CLOSE_LONG,
        symbol="BTCUSDT",
        side="sell",
        entry_type=EntryType.MARKET,
        quantity=Decimal("1"),
        reference_price=Decimal("100"),
        generated_at=now,
    )
    close_decision = __import__("asyncio").run(engine.assess(close_intent, state, snapshot))

    assert close_decision.decision.value == "allow"
