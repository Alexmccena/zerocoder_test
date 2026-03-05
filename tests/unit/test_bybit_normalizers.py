from __future__ import annotations

from trading_bot.adapters.exchanges.bybit.normalizers import (
    normalize_instrument,
    normalize_open_interest,
    normalize_private_message,
    normalize_public_message,
    normalize_rest_klines,
)
from trading_bot.marketdata.events import KlineEvent, OpenInterestEvent, OrderBookEvent, OrderUpdateEvent, TradeEvent, WalletEvent


def test_normalize_instrument_maps_filters() -> None:
    instrument = normalize_instrument(
        {
            "symbol": "BTCUSDT",
            "quoteCoin": "USDT",
            "baseCoin": "BTC",
            "status": "Trading",
            "priceScale": "2",
            "priceFilter": {"tickSize": "0.10"},
            "lotSizeFilter": {
                "qtyStep": "0.001",
                "minOrderQty": "0.001",
                "minNotionalValue": "5",
                "maxOrderQty": "100",
            },
            "leverageFilter": {"maxLeverage": "25"},
        }
    )

    assert instrument.symbol == "BTCUSDT"
    assert str(instrument.tick_size) == "0.10"
    assert str(instrument.max_leverage) == "25"


def test_normalize_public_messages() -> None:
    orderbook_events = normalize_public_message(
        {
            "topic": "orderbook.50.BTCUSDT",
            "type": "snapshot",
            "ts": 1710000000000,
            "data": {
                "s": "BTCUSDT",
                "b": [["60000", "1.5"]],
                "a": [["60001", "2.0"]],
                "u": 1,
                "seq": 10,
            },
        }
    )
    trades = normalize_public_message(
        {
            "topic": "publicTrade.BTCUSDT",
            "ts": 1710000000000,
            "data": [{"s": "BTCUSDT", "T": 1710000000000, "S": "Buy", "p": "60000", "v": "0.2", "i": "abc"}],
        }
    )
    klines = normalize_public_message(
        {
            "topic": "kline.1.BTCUSDT",
            "ts": 1710000000000,
            "data": [
                {
                    "start": 1710000000000,
                    "end": 1710000059999,
                    "open": "60000",
                    "high": "60010",
                    "low": "59990",
                    "close": "60005",
                    "volume": "12.3",
                    "turnover": "738000",
                    "confirm": False,
                }
            ],
        }
    )

    assert isinstance(orderbook_events[0], OrderBookEvent)
    assert orderbook_events[0].symbol == "BTCUSDT"
    assert isinstance(trades[0], TradeEvent)
    assert trades[0].side == "buy"
    assert isinstance(klines[0], KlineEvent)
    assert klines[0].symbol == "BTCUSDT"
    assert klines[0].interval == "1m"


def test_normalize_open_interest_payload() -> None:
    event = normalize_open_interest(
        "BTCUSDT",
        {"list": [{"openInterest": "123.4", "timestamp": "1710000000000", "intervalTime": "5min"}]},
    )

    assert isinstance(event, OpenInterestEvent)
    assert str(event.open_interest) == "123.4"
    assert event.interval == "5m"


def test_normalize_rest_klines_canonicalizes_intervals() -> None:
    events = normalize_rest_klines(
        "BTCUSDT",
        interval="5",
        rows=[["1710000000000", "100", "101", "99", "100.5", "12.3", "1230"]],
    )

    assert events[0].interval == "5m"


def test_normalize_private_messages() -> None:
    wallet_events = normalize_private_message(
        {
            "topic": "wallet",
            "data": [
                {
                    "creationTime": 1710000000000,
                    "accountType": "UNIFIED",
                    "totalMarginBalance": "1000",
                    "coin": [{"walletBalance": "950", "availableToWithdraw": "900", "equity": "980", "unrealisedPnl": "30"}],
                }
            ],
        }
    )
    order_events = normalize_private_message(
        {
            "topic": "order",
            "data": [
                {
                    "symbol": "BTCUSDT",
                    "orderId": "oid-1",
                    "side": "Buy",
                    "orderType": "Limit",
                    "orderStatus": "New",
                    "qty": "0.1",
                    "cumExecQty": "0",
                    "createdTime": 1710000000000,
                    "updatedTime": 1710000001000,
                }
            ],
        }
    )

    assert isinstance(wallet_events[0], WalletEvent)
    assert wallet_events[0].account_type == "UNIFIED"
    assert isinstance(order_events[0], OrderUpdateEvent)
    assert order_events[0].order.exchange_order_id == "oid-1"
    assert order_events[0].order.side == "buy"
    assert order_events[0].order.status == "working"
