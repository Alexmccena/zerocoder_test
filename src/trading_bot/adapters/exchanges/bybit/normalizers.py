from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from trading_bot.domain.enums import ExchangeName, MarketType, PositionMode
from trading_bot.domain.models import AccountState, FillState, Instrument, OrderState, PositionState
from trading_bot.marketdata.events import (
    ExecutionEvent,
    FundingRateEvent,
    KlineEvent,
    LiquidationEvent,
    OpenInterestEvent,
    OrderBookEvent,
    OrderBookLevel,
    OrderUpdateEvent,
    PositionUpdateEvent,
    TickerEvent,
    TradeEvent,
    WalletEvent,
)
from trading_bot.timeframes import canonicalize_interval, interval_to_minutes


def to_decimal(value: Any, default: str = "0") -> Decimal:
    return Decimal(str(value if value not in (None, "") else default))


def from_millis(value: Any) -> datetime:
    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)


def _level_list(levels: list[list[str]]) -> list[OrderBookLevel]:
    return [OrderBookLevel(price=to_decimal(price), size=to_decimal(size)) for price, size in levels]


def normalize_instrument(payload: dict[str, Any]) -> Instrument:
    lot_filter = payload.get("lotSizeFilter", {})
    price_filter = payload.get("priceFilter", {})
    leverage_filter = payload.get("leverageFilter", {})
    return Instrument(
        exchange_name=ExchangeName.BYBIT,
        symbol=payload["symbol"],
        market_type=MarketType.LINEAR_PERP,
        tick_size=to_decimal(price_filter.get("tickSize", "0")),
        lot_size=to_decimal(lot_filter.get("qtyStep", "0")),
        min_quantity=to_decimal(lot_filter.get("minOrderQty", "0")),
        min_notional=to_decimal(lot_filter.get("minNotionalValue", "0")),
        max_order_quantity=to_decimal(lot_filter.get("maxOrderQty", "0")),
        max_leverage=to_decimal(leverage_filter.get("maxLeverage", "0")),
        quote_asset=payload.get("quoteCoin", "USDT"),
        base_asset=payload.get("baseCoin", ""),
        status=payload.get("status", "Trading"),
        price_scale=int(payload["priceScale"]) if payload.get("priceScale") is not None else None,
        raw_payload=payload,
    )


def normalize_account_snapshot(payload: dict[str, Any]) -> AccountState:
    accounts = payload.get("list", [])
    account = accounts[0] if accounts else {}
    coin_rows = account.get("coin", [])
    coin = coin_rows[0] if coin_rows else {}
    return AccountState(
        exchange_name=ExchangeName.BYBIT,
        equity=to_decimal(account.get("totalEquity", coin.get("equity", "0"))),
        available_balance=to_decimal(account.get("totalAvailableBalance", coin.get("availableToWithdraw", "0"))),
        wallet_balance=to_decimal(account.get("totalWalletBalance", coin.get("walletBalance", "0"))),
        margin_balance=to_decimal(account.get("totalMarginBalance", coin.get("walletBalance", "0"))),
        unrealized_pnl=to_decimal(account.get("totalPerpUPL", coin.get("unrealisedPnl", "0"))),
        account_type=account.get("accountType", "UNIFIED"),
        position_mode=PositionMode.ONE_WAY,
        raw_payload=payload,
    )


def normalize_position(payload: dict[str, Any]) -> PositionState:
    updated_time = payload.get("updatedTime", payload.get("createdTime", 0) or 0)
    return PositionState(
        exchange_name=ExchangeName.BYBIT,
        symbol=payload["symbol"],
        side=payload.get("side", ""),
        quantity=to_decimal(payload.get("size", "0")),
        entry_price=to_decimal(payload.get("avgPrice", "0")),
        mark_price=to_decimal(payload.get("markPrice")) if payload.get("markPrice") not in (None, "") else None,
        leverage=to_decimal(payload.get("leverage", "1")),
        realized_pnl=to_decimal(payload.get("cumRealisedPnl", "0")),
        unrealized_pnl=to_decimal(payload.get("unrealisedPnl", "0")),
        status="open" if to_decimal(payload.get("size", "0")) > 0 else "closed",
        raw_payload=payload,
        updated_at=from_millis(updated_time),
    )


def normalize_order(payload: dict[str, Any]) -> OrderState:
    created_time = payload.get("createdTime", payload.get("updatedTime", 0) or 0)
    updated_time = payload.get("updatedTime", payload.get("createdTime", 0) or 0)
    avg_price = payload.get("avgPrice")
    return OrderState(
        order_id=str(payload.get("orderId", payload.get("orderLinkId", ""))),
        exchange_name=ExchangeName.BYBIT,
        symbol=payload["symbol"],
        side=payload.get("side", ""),
        order_type=payload.get("orderType", ""),
        status=payload.get("orderStatus", payload.get("cancelType", "unknown")),
        quantity=to_decimal(payload.get("qty", "0")),
        filled_quantity=to_decimal(payload.get("cumExecQty", "0")),
        average_price=to_decimal(avg_price) if avg_price not in (None, "") else None,
        exchange_order_id=payload.get("orderId"),
        time_in_force=payload.get("timeInForce"),
        raw_payload=payload,
        created_at=from_millis(created_time),
        updated_at=from_millis(updated_time),
    )


def normalize_fill(payload: dict[str, Any]) -> FillState:
    return FillState(
        order_id=str(payload.get("orderId", "")),
        exchange_name=ExchangeName.BYBIT,
        symbol=payload.get("symbol", ""),
        side=payload.get("side", ""),
        price=to_decimal(payload.get("execPrice", "0")),
        quantity=to_decimal(payload.get("execQty", "0")),
        fee=to_decimal(payload.get("execFee", "0")),
        liquidity_type=payload.get("execType", "unknown"),
        exchange_fill_id=payload.get("execId"),
        raw_payload=payload,
        filled_at=from_millis(payload.get("execTime", payload.get("createdTime", 0) or 0)),
    )


def normalize_public_message(message: dict[str, Any]) -> list[object]:
    topic = str(message.get("topic", ""))
    data = message.get("data")
    ts = from_millis(message.get("ts", 0))
    if topic.startswith("orderbook.") and isinstance(data, dict):
        return [
            OrderBookEvent(
                exchange_name=ExchangeName.BYBIT,
                symbol=data["s"],
                event_ts=ts,
                depth=int(topic.split(".")[1]),
                sequence=int(data["seq"]) if data.get("seq") is not None else None,
                update_id=int(data["u"]) if data.get("u") is not None else None,
                is_snapshot=str(message.get("type", "delta")).lower() == "snapshot",
                bids=_level_list(data.get("b", [])),
                asks=_level_list(data.get("a", [])),
                raw_payload=message,
            )
        ]
    if topic.startswith("publicTrade.") and isinstance(data, list):
        return [
            TradeEvent(
                exchange_name=ExchangeName.BYBIT,
                symbol=trade["s"],
                event_ts=from_millis(trade.get("T", message.get("ts", 0))),
                trade_id=str(trade.get("i", "")),
                side=trade.get("S", ""),
                price=to_decimal(trade.get("p", "0")),
                quantity=to_decimal(trade.get("v", "0")),
                raw_payload=trade,
            )
            for trade in data
        ]
    if topic.startswith("tickers.") and isinstance(data, dict):
        return [
            TickerEvent(
                exchange_name=ExchangeName.BYBIT,
                symbol=data["symbol"],
                event_ts=ts,
                bid_price=to_decimal(data.get("bid1Price")) if data.get("bid1Price") not in (None, "") else None,
                ask_price=to_decimal(data.get("ask1Price")) if data.get("ask1Price") not in (None, "") else None,
                last_price=to_decimal(data.get("lastPrice")) if data.get("lastPrice") not in (None, "") else None,
                mark_price=to_decimal(data.get("markPrice")) if data.get("markPrice") not in (None, "") else None,
                index_price=to_decimal(data.get("indexPrice")) if data.get("indexPrice") not in (None, "") else None,
                open_interest=to_decimal(data.get("openInterest")) if data.get("openInterest") not in (None, "") else None,
                funding_rate=to_decimal(data.get("fundingRate")) if data.get("fundingRate") not in (None, "") else None,
                raw_payload=message,
            )
        ]
    if topic.startswith("kline.") and isinstance(data, list):
        topic_parts = topic.split(".")
        topic_interval = topic_parts[1] if len(topic_parts) > 1 else None
        topic_symbol = topic_parts[2] if len(topic_parts) > 2 else None
        return [
            KlineEvent(
                exchange_name=ExchangeName.BYBIT,
                symbol=row.get("symbol", topic_symbol or ""),
                event_ts=ts,
                interval=canonicalize_interval(row.get("interval", topic_interval or "")),
                start_at=from_millis(row["start"]),
                end_at=from_millis(row["end"]),
                open_price=to_decimal(row["open"]),
                high_price=to_decimal(row["high"]),
                low_price=to_decimal(row["low"]),
                close_price=to_decimal(row["close"]),
                volume=to_decimal(row["volume"]),
                turnover=to_decimal(row.get("turnover")) if row.get("turnover") not in (None, "") else None,
                is_closed=bool(row.get("confirm", False)),
                raw_payload=row,
            )
            for row in data
        ]
    if topic.startswith("allLiquidation.") and isinstance(data, list):
        return [
            LiquidationEvent(
                exchange_name=ExchangeName.BYBIT,
                symbol=row["s"],
                event_ts=from_millis(row.get("T", message.get("ts", 0))),
                side=row.get("S", ""),
                price=to_decimal(row.get("p", "0")),
                quantity=to_decimal(row.get("v", "0")),
                raw_payload=row,
            )
            for row in data
        ]
    return []


def normalize_open_interest(symbol: str, payload: dict[str, Any]) -> OpenInterestEvent | None:
    rows = payload.get("list", [])
    if not rows:
        return None
    row = rows[0]
    return OpenInterestEvent(
        exchange_name=ExchangeName.BYBIT,
        symbol=symbol,
        event_ts=from_millis(row.get("timestamp", 0)),
        open_interest=to_decimal(row.get("openInterest", "0")),
        interval=canonicalize_interval(row.get("intervalTime", "5m")),
        raw_payload=row,
    )


def normalize_funding_rate(symbol: str, payload: dict[str, Any]) -> FundingRateEvent | None:
    rows = payload.get("list", [])
    if not rows:
        return None
    row = rows[0]
    next_funding = row.get("fundingRateTimestamp")
    return FundingRateEvent(
        exchange_name=ExchangeName.BYBIT,
        symbol=symbol,
        event_ts=from_millis(row.get("fundingRateTimestamp", 0)),
        funding_rate=to_decimal(row.get("fundingRate", "0")),
        next_funding_at=from_millis(next_funding) if next_funding not in (None, "") else None,
        raw_payload=row,
    )


def normalize_rest_klines(symbol: str, *, interval: str | int, rows: list[Any]) -> list[KlineEvent]:
    canonical_interval = canonicalize_interval(interval)
    interval_minutes = interval_to_minutes(canonical_interval)
    events: list[KlineEvent] = []
    for row in rows:
        if isinstance(row, dict):
            start_raw = row.get("start") or row.get("startTime")
            end_raw = row.get("end") or row.get("endTime")
            open_price = row.get("open")
            high_price = row.get("high")
            low_price = row.get("low")
            close_price = row.get("close")
            volume = row.get("volume")
            turnover = row.get("turnover")
        else:
            values = list(row)
            if len(values) < 6:
                continue
            start_raw = values[0]
            end_raw = None
            open_price = values[1]
            high_price = values[2]
            low_price = values[3]
            close_price = values[4]
            volume = values[5]
            turnover = values[6] if len(values) > 6 else None

        if start_raw in (None, ""):
            continue
        start_at = from_millis(start_raw)
        end_at = from_millis(end_raw) if end_raw not in (None, "") else start_at + timedelta(minutes=interval_minutes)
        events.append(
            KlineEvent(
                exchange_name=ExchangeName.BYBIT,
                symbol=symbol,
                event_ts=end_at,
                interval=canonical_interval,
                start_at=start_at,
                end_at=end_at,
                open_price=to_decimal(open_price, "0"),
                high_price=to_decimal(high_price, "0"),
                low_price=to_decimal(low_price, "0"),
                close_price=to_decimal(close_price, "0"),
                volume=to_decimal(volume, "0"),
                turnover=to_decimal(turnover) if turnover not in (None, "") else None,
                is_closed=True,
                raw_payload=row if isinstance(row, dict) else {"row": values},
            )
        )
    return events


def normalize_private_message(message: dict[str, Any]) -> list[object]:
    topic = str(message.get("topic", ""))
    data = message.get("data", [])
    rows = data if isinstance(data, list) else [data]
    if topic == "wallet":
        events: list[object] = []
        for row in rows:
            coin_rows = row.get("coin", [])
            coin = coin_rows[0] if coin_rows else {}
            events.append(
                WalletEvent(
                    exchange_name=ExchangeName.BYBIT,
                    event_ts=from_millis(row.get("creationTime", 0)),
                    wallet_balance=to_decimal(coin.get("walletBalance", row.get("totalWalletBalance", "0"))),
                    available_balance=to_decimal(coin.get("availableToWithdraw", row.get("totalAvailableBalance", "0"))),
                    equity=to_decimal(coin.get("equity", row.get("totalEquity", "0"))),
                    margin_balance=to_decimal(row.get("totalMarginBalance", coin.get("walletBalance", "0"))),
                    unrealized_pnl=to_decimal(coin.get("unrealisedPnl", row.get("totalPerpUPL", "0"))),
                    account_type=row.get("accountType", "UNIFIED"),
                    raw_payload=row,
                )
            )
        return events
    if topic == "order":
        return [
            OrderUpdateEvent(
                exchange_name=ExchangeName.BYBIT,
                event_ts=from_millis(row.get("updatedTime", row.get("createdTime", 0) or 0)),
                order=normalize_order(row),
                raw_payload=row,
            )
            for row in rows
        ]
    if topic == "execution":
        return [
            ExecutionEvent(
                exchange_name=ExchangeName.BYBIT,
                event_ts=from_millis(row.get("execTime", 0)),
                symbol=row.get("symbol", ""),
                order_id=str(row.get("orderId", "")),
                exchange_order_id=row.get("orderId"),
                exchange_fill_id=row.get("execId"),
                side=row.get("side", ""),
                price=to_decimal(row.get("execPrice", "0")),
                quantity=to_decimal(row.get("execQty", "0")),
                fee=to_decimal(row.get("execFee", "0")),
                liquidity_type=row.get("execType", "unknown"),
                filled_at=from_millis(row.get("execTime", 0)),
                raw_payload=row,
            )
            for row in rows
        ]
    if topic == "position":
        return [
            PositionUpdateEvent(
                exchange_name=ExchangeName.BYBIT,
                event_ts=from_millis(row.get("updatedTime", row.get("createdTime", 0) or 0)),
                position=normalize_position(row),
                raw_payload=row,
            )
            for row in rows
        ]
    return []
