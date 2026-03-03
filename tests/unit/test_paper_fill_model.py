from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from trading_bot.config.schema import ExecutionConfig, PaperConfig
from trading_bot.domain.enums import ExchangeName, ExecutionVenueKind
from trading_bot.domain.models import MarketSnapshot, OrderState
from trading_bot.marketdata.events import OrderBookEvent, OrderBookLevel
from trading_bot.paper.fill_model import PaperFillModel


def test_market_fill_walks_orderbook_depth() -> None:
    now = datetime.now(timezone.utc)
    model = PaperFillModel(execution=ExecutionConfig(market_slippage_guard_bps=100.0), paper=PaperConfig())
    snapshot = MarketSnapshot(
        symbol="BTCUSDT",
        as_of=now,
        orderbook=OrderBookEvent(
            exchange_name=ExchangeName.BYBIT,
            symbol="BTCUSDT",
            event_ts=now,
            depth=50,
            bids=[OrderBookLevel(price=Decimal("99"), size=Decimal("5"))],
            asks=[
                OrderBookLevel(price=Decimal("100"), size=Decimal("1")),
                OrderBookLevel(price=Decimal("101"), size=Decimal("2")),
            ],
        ),
    )
    order = OrderState(
        order_id="o1",
        exchange_name=ExchangeName.BYBIT,
        execution_venue=ExecutionVenueKind.PAPER,
        symbol="BTCUSDT",
        side="buy",
        order_type="market",
        status="new",
        quantity=Decimal("2"),
        submitted_at=now,
    )

    attempt = model.simulate_market_fill(order=order, snapshot=snapshot, as_of=now)

    assert attempt.fill is not None
    assert attempt.fill.price == Decimal("100.5")


def test_limit_fill_respects_visible_ratio_for_partial_fill() -> None:
    now = datetime.now(timezone.utc)
    model = PaperFillModel(execution=ExecutionConfig(), paper=PaperConfig())
    snapshot = MarketSnapshot(
        symbol="BTCUSDT",
        as_of=now,
        orderbook=OrderBookEvent(
            exchange_name=ExchangeName.BYBIT,
            symbol="BTCUSDT",
            event_ts=now,
            depth=50,
            bids=[OrderBookLevel(price=Decimal("99"), size=Decimal("5"))],
            asks=[OrderBookLevel(price=Decimal("100"), size=Decimal("4"))],
        ),
    )
    order = OrderState(
        order_id="o2",
        exchange_name=ExchangeName.BYBIT,
        execution_venue=ExecutionVenueKind.PAPER,
        symbol="BTCUSDT",
        side="buy",
        order_type="limit",
        status="working",
        quantity=Decimal("2"),
        price=Decimal("100"),
        submitted_at=now,
    )

    attempt = model.simulate_limit_fill(order=order, snapshot=snapshot, as_of=now)

    assert attempt.fill is not None
    assert attempt.fill.quantity == Decimal("1")
