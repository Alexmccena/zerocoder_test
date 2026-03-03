from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from trading_bot.config.schema import AppSettings
from trading_bot.domain.enums import EntryType, ExchangeName, ExecutionVenueKind, MarketType, PositionMode
from trading_bot.domain.models import ExecutionPlan, Instrument, MarketSnapshot, OrderIntent
from trading_bot.marketdata.events import OrderBookEvent, OrderBookLevel
from trading_bot.observability.metrics import AppMetrics
from trading_bot.paper.venue import PaperVenue


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
            "execution": {"market_slippage_guard_bps": 100.0},
            "paper": {"initial_equity_usdt": "10", "fill_latency_ms": 0},
            "risk": {"max_open_positions": 2, "risk_per_trade": 0.01, "max_daily_loss": 0.2},
            "llm": {"enabled": False, "provider": "none", "model_name": "", "timeout_seconds": 10},
        }
    )


def _build_snapshot(*, bid: str, ask: str, size: str = "10") -> MarketSnapshot:
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
            bids=[OrderBookLevel(price=Decimal(bid), size=Decimal(size))],
            asks=[OrderBookLevel(price=Decimal(ask), size=Decimal(size))],
        ),
    )


def test_paper_venue_stop_market_fill_cancels_take_profit_sibling() -> None:
    venue = PaperVenue(config=_build_settings(), metrics=AppMetrics())
    opened_at = datetime.now(timezone.utc)

    entry_result = __import__("asyncio").run(
        venue.submit(
            ExecutionPlan(
                execution_venue=ExecutionVenueKind.PAPER,
                intent_id="intent-1",
                entry_order=OrderIntent(
                    intent_id="intent-1",
                    exchange_name=ExchangeName.BYBIT,
                    execution_venue=ExecutionVenueKind.PAPER,
                    symbol="BTCUSDT",
                    side="buy",
                    order_type=EntryType.MARKET.value,
                    quantity=Decimal("1"),
                    submitted_at=opened_at,
                    metadata={"order_role": "entry"},
                ),
            )
        )
    )
    __import__("asyncio").run(venue.process_market_event("BTCUSDT", _build_snapshot(bid="99.9", ask="100.0"), opened_at))

    stop_result = __import__("asyncio").run(
        venue.submit(
            ExecutionPlan(
                execution_venue=ExecutionVenueKind.PAPER,
                intent_id="intent-1",
                entry_order=OrderIntent(
                    intent_id="intent-1",
                    exchange_name=ExchangeName.BYBIT,
                    execution_venue=ExecutionVenueKind.PAPER,
                    symbol="BTCUSDT",
                    side="sell",
                    order_type="stop_market",
                    quantity=Decimal("1"),
                    stop_price=Decimal("99.5"),
                    reduce_only=True,
                    submitted_at=opened_at,
                    metadata={"order_role": "stop_loss"},
                ),
            )
        )
    )
    take_profit_result = __import__("asyncio").run(
        venue.submit(
            ExecutionPlan(
                execution_venue=ExecutionVenueKind.PAPER,
                intent_id="intent-1",
                entry_order=OrderIntent(
                    intent_id="intent-1",
                    exchange_name=ExchangeName.BYBIT,
                    execution_venue=ExecutionVenueKind.PAPER,
                    symbol="BTCUSDT",
                    side="sell",
                    order_type="limit",
                    quantity=Decimal("1"),
                    price=Decimal("101"),
                    reduce_only=True,
                    submitted_at=opened_at,
                    metadata={"order_role": "take_profit"},
                ),
            )
        )
    )

    result = __import__("asyncio").run(
        venue.process_market_event("BTCUSDT", _build_snapshot(bid="99.4", ask="99.5"), datetime.now(timezone.utc))
    )

    assert entry_result.orders[0].status == "new"
    assert stop_result.orders[0].raw_payload["order_role"] == "stop_loss"
    assert take_profit_result.orders[0].raw_payload["order_role"] == "take_profit"
    assert [order.raw_payload.get("order_role") for order in result.orders] == ["stop_loss", "take_profit"]
    assert [order.status for order in result.orders] == ["filled", "cancelled"]
    snapshot = __import__("asyncio").run(venue.snapshot_state())
    assert snapshot.open_positions == []
    assert snapshot.open_orders == []


def test_paper_venue_reduce_only_order_does_not_flip_position() -> None:
    venue = PaperVenue(config=_build_settings(), metrics=AppMetrics())
    opened_at = datetime.now(timezone.utc)

    __import__("asyncio").run(
        venue.submit(
            ExecutionPlan(
                execution_venue=ExecutionVenueKind.PAPER,
                intent_id="intent-1",
                entry_order=OrderIntent(
                    intent_id="intent-1",
                    exchange_name=ExchangeName.BYBIT,
                    execution_venue=ExecutionVenueKind.PAPER,
                    symbol="BTCUSDT",
                    side="buy",
                    order_type=EntryType.MARKET.value,
                    quantity=Decimal("1"),
                    submitted_at=opened_at,
                    metadata={"order_role": "entry"},
                ),
            )
        )
    )
    __import__("asyncio").run(venue.process_market_event("BTCUSDT", _build_snapshot(bid="99.9", ask="100.0"), opened_at))

    __import__("asyncio").run(
        venue.submit(
            ExecutionPlan(
                execution_venue=ExecutionVenueKind.PAPER,
                intent_id="intent-2",
                entry_order=OrderIntent(
                    intent_id="intent-2",
                    exchange_name=ExchangeName.BYBIT,
                    execution_venue=ExecutionVenueKind.PAPER,
                    symbol="BTCUSDT",
                    side="sell",
                    order_type=EntryType.MARKET.value,
                    quantity=Decimal("2"),
                    reduce_only=True,
                    submitted_at=opened_at,
                    metadata={},
                ),
            )
        )
    )

    result = __import__("asyncio").run(
        venue.process_market_event("BTCUSDT", _build_snapshot(bid="99.8", ask="99.9"), datetime.now(timezone.utc))
    )

    assert len(result.fills) == 1
    assert result.fills[0].quantity == Decimal("1")
    snapshot = __import__("asyncio").run(venue.snapshot_state())
    assert snapshot.open_positions == []
