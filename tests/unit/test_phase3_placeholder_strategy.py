from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from trading_bot.config.schema import AppSettings
from trading_bot.domain.enums import ExchangeName, ExecutionVenueKind, MarketType, PositionMode, TradeAction
from trading_bot.domain.models import FeatureSnapshot, Instrument, MarketSnapshot, PositionState, RuntimeState
from trading_bot.marketdata.events import KlineEvent, OrderBookEvent, OrderBookLevel, TickerEvent
from trading_bot.strategies.phase3_placeholder import Phase3PlaceholderStrategy


def _build_settings(*, min_imbalance: float = 0.1) -> AppSettings:
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
            "strategy": {"placeholder": {"min_imbalance": min_imbalance}},
            "risk": {"max_open_positions": 2, "risk_per_trade": 0.1, "max_daily_loss": 0.2},
            "llm": {"enabled": False, "provider": "none", "model_name": "", "timeout_seconds": 10},
        }
    )


def _build_snapshot() -> MarketSnapshot:
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
            bids=[OrderBookLevel(price=Decimal("100"), size=Decimal("2"))],
            asks=[OrderBookLevel(price=Decimal("101"), size=Decimal("2"))],
        ),
        ticker=TickerEvent(
            exchange_name=ExchangeName.BYBIT,
            symbol="BTCUSDT",
            event_ts=now,
            last_price=Decimal("100.5"),
        ),
        closed_klines_by_interval={
            "1m": KlineEvent(
                exchange_name=ExchangeName.BYBIT,
                symbol="BTCUSDT",
                event_ts=now,
                interval="1m",
                start_at=now,
                end_at=now,
                open_price=Decimal("99"),
                high_price=Decimal("101"),
                low_price=Decimal("98"),
                close_price=Decimal("100.5"),
                volume=Decimal("10"),
                is_closed=True,
            )
        },
    )


async def test_phase3_strategy_opens_and_closes_positions() -> None:
    settings = _build_settings()
    state = RuntimeState(run_session_id="run-1", run_mode=settings.runtime.mode, execution_venue=ExecutionVenueKind.PAPER)
    strategy = Phase3PlaceholderStrategy(config=settings, runtime_state_provider=lambda: state)
    snapshot = _build_snapshot()

    open_intents = await strategy.evaluate(
        snapshot,
        FeatureSnapshot(
            symbol="BTCUSDT",
            last_close_change_bps=Decimal("10"),
            top5_imbalance=0.2,
            has_fresh_orderbook=True,
        ),
    )

    assert open_intents[0].action == TradeAction.OPEN_LONG

    state.open_positions["BTCUSDT"] = PositionState(
        exchange_name=ExchangeName.BYBIT,
        execution_venue=ExecutionVenueKind.PAPER,
        symbol="BTCUSDT",
        side="long",
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
    )

    close_intents = await strategy.evaluate(
        snapshot,
        FeatureSnapshot(
            symbol="BTCUSDT",
            last_close_change_bps=Decimal("-10"),
            top5_imbalance=-0.2,
            has_fresh_orderbook=True,
        ),
    )

    assert close_intents[0].action == TradeAction.CLOSE_LONG


async def test_phase3_strategy_ignores_imbalance_when_min_imbalance_is_zero() -> None:
    settings = _build_settings(min_imbalance=0.0)
    state = RuntimeState(run_session_id="run-1", run_mode=settings.runtime.mode, execution_venue=ExecutionVenueKind.PAPER)
    strategy = Phase3PlaceholderStrategy(config=settings, runtime_state_provider=lambda: state)
    snapshot = _build_snapshot()

    intents = await strategy.evaluate(
        snapshot,
        FeatureSnapshot(
            symbol="BTCUSDT",
            last_close_change_bps=Decimal("10"),
            top5_imbalance=-0.9,
            has_fresh_orderbook=True,
        ),
    )

    assert intents[0].action == TradeAction.OPEN_LONG


async def test_phase3_strategy_allows_open_without_fresh_orderbook_in_test_mode() -> None:
    settings = _build_settings(min_imbalance=0.0)
    state = RuntimeState(run_session_id="run-1", run_mode=settings.runtime.mode, execution_venue=ExecutionVenueKind.PAPER)
    strategy = Phase3PlaceholderStrategy(config=settings, runtime_state_provider=lambda: state)
    snapshot = _build_snapshot()

    intents = await strategy.evaluate(
        snapshot,
        FeatureSnapshot(
            symbol="BTCUSDT",
            last_close_change_bps=Decimal("10"),
            top5_imbalance=0.0,
            has_fresh_orderbook=False,
        ),
    )

    assert intents[0].action == TradeAction.OPEN_LONG


async def test_phase3_strategy_uses_ticker_bid_ask_when_last_price_missing() -> None:
    settings = _build_settings()
    state = RuntimeState(run_session_id="run-1", run_mode=settings.runtime.mode, execution_venue=ExecutionVenueKind.PAPER)
    strategy = Phase3PlaceholderStrategy(config=settings, runtime_state_provider=lambda: state)
    snapshot = _build_snapshot().model_copy(deep=True)
    now = snapshot.as_of
    snapshot.orderbook = OrderBookEvent(
        exchange_name=ExchangeName.BYBIT,
        symbol="BTCUSDT",
        event_ts=now,
        depth=50,
        bids=[OrderBookLevel(price=Decimal("100"), size=Decimal("2"))],
        asks=[OrderBookLevel(price=Decimal("101"), size=Decimal("2"))],
    )
    snapshot.ticker = TickerEvent(
        exchange_name=ExchangeName.BYBIT,
        symbol="BTCUSDT",
        event_ts=now,
        bid_price=Decimal("200"),
        ask_price=Decimal("200.5"),
    )

    intents = await strategy.evaluate(
        snapshot,
        FeatureSnapshot(
            symbol="BTCUSDT",
            last_close_change_bps=Decimal("10"),
            top5_imbalance=0.2,
            has_fresh_orderbook=True,
        ),
    )

    assert intents[0].action == TradeAction.OPEN_LONG
    assert intents[0].reference_price == Decimal("200.25")


async def test_phase3_strategy_prefers_kline_reference_over_orderbook_when_ticker_missing() -> None:
    settings = _build_settings()
    state = RuntimeState(run_session_id="run-1", run_mode=settings.runtime.mode, execution_venue=ExecutionVenueKind.PAPER)
    strategy = Phase3PlaceholderStrategy(config=settings, runtime_state_provider=lambda: state)
    snapshot = _build_snapshot().model_copy(deep=True)
    now = snapshot.as_of
    snapshot.ticker = None
    snapshot.orderbook = OrderBookEvent(
        exchange_name=ExchangeName.BYBIT,
        symbol="BTCUSDT",
        event_ts=now,
        depth=50,
        bids=[OrderBookLevel(price=Decimal("100"), size=Decimal("2"))],
        asks=[OrderBookLevel(price=Decimal("101"), size=Decimal("2"))],
    )
    snapshot.closed_klines_by_interval["1m"] = KlineEvent(
        exchange_name=ExchangeName.BYBIT,
        symbol="BTCUSDT",
        event_ts=now,
        interval="1m",
        start_at=now,
        end_at=now,
        open_price=Decimal("299"),
        high_price=Decimal("301"),
        low_price=Decimal("298"),
        close_price=Decimal("300"),
        volume=Decimal("10"),
        is_closed=True,
    )

    intents = await strategy.evaluate(
        snapshot,
        FeatureSnapshot(
            symbol="BTCUSDT",
            last_close_change_bps=Decimal("10"),
            top5_imbalance=0.2,
            has_fresh_orderbook=True,
        ),
    )

    assert intents[0].action == TradeAction.OPEN_LONG
    assert intents[0].reference_price == Decimal("300")
