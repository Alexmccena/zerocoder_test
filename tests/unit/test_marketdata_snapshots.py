from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading_bot.domain.enums import ExchangeName, MarketType
from trading_bot.domain.models import Instrument
from trading_bot.marketdata.events import KlineEvent, OrderBookEvent, OrderBookLevel
from trading_bot.marketdata.snapshots import FeatureProvider, MarketSnapshotBuilder


def test_snapshot_builder_and_feature_provider_compute_expected_values() -> None:
    now = datetime.now(timezone.utc)
    builder = MarketSnapshotBuilder(stale_after_seconds=2)
    provider = FeatureProvider(timeframe="1m")
    builder.register_instruments(
        [
            Instrument(
                exchange_name=ExchangeName.BYBIT,
                symbol="BTCUSDT",
                market_type=MarketType.LINEAR_PERP,
                tick_size=Decimal("0.1"),
                lot_size=Decimal("0.001"),
                min_quantity=Decimal("0.001"),
                quote_asset="USDT",
                base_asset="BTC",
            )
        ]
    )
    first_orderbook = OrderBookEvent(
        exchange_name=ExchangeName.BYBIT,
        symbol="BTCUSDT",
        event_ts=now,
        depth=50,
        bids=[OrderBookLevel(price=Decimal("100"), size=Decimal("6"))],
        asks=[OrderBookLevel(price=Decimal("101"), size=Decimal("4"))],
    )
    builder.apply_event(first_orderbook)
    provider.observe(first_orderbook, builder.build("BTCUSDT", as_of=now))
    first_kline = KlineEvent(
        exchange_name=ExchangeName.BYBIT,
        symbol="BTCUSDT",
        event_ts=now,
        interval="1m",
        start_at=now - timedelta(minutes=2),
        end_at=now - timedelta(minutes=1),
        open_price=Decimal("90"),
        high_price=Decimal("101"),
        low_price=Decimal("89"),
        close_price=Decimal("100"),
        volume=Decimal("10"),
        is_closed=True,
    )
    builder.apply_event(first_kline)
    provider.observe(first_kline, builder.build("BTCUSDT", as_of=now))
    provider.compute(builder.build("BTCUSDT", as_of=now))
    second_kline = KlineEvent(
        exchange_name=ExchangeName.BYBIT,
        symbol="BTCUSDT",
        event_ts=now + timedelta(minutes=1),
        interval="1m",
        start_at=now - timedelta(minutes=1),
        end_at=now,
        open_price=Decimal("100"),
        high_price=Decimal("111"),
        low_price=Decimal("99"),
        close_price=Decimal("110"),
        volume=Decimal("10"),
        is_closed=True,
    )
    builder.apply_event(second_kline)
    provider.observe(second_kline, builder.build("BTCUSDT", as_of=now + timedelta(minutes=1)))
    snapshot = builder.build("BTCUSDT", as_of=now + timedelta(seconds=1))
    features = provider.compute(snapshot)

    assert snapshot.instrument is not None
    assert snapshot.data_is_stale is False
    assert round(features.top5_imbalance, 2) == 0.20
    assert features.last_close_change_bps == Decimal("1000")
