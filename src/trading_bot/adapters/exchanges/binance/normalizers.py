from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from trading_bot.domain.enums import ExchangeName, MarketType, PositionMode
from trading_bot.domain.models import AccountState, Instrument, OrderState, PositionState
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


def normalize_order_side(value: str | None) -> str:
    side = (value or "").lower()
    if side == "buy":
        return "buy"
    if side == "sell":
        return "sell"
    return side


def normalize_position_side(*, position_amt: Decimal, position_side: str | None) -> str:
    side = (position_side or "").upper()
    if side == "LONG":
        return "long"
    if side == "SHORT":
        return "short"
    if position_amt > 0:
        return "long"
    if position_amt < 0:
        return "short"
    return "closed"


def normalize_order_type(value: str | None) -> str:
    order_type = (value or "").upper()
    mapping = {
        "MARKET": "market",
        "LIMIT": "limit",
        "STOP_MARKET": "stop_market",
        "STOP": "stop_market",
        "TAKE_PROFIT_MARKET": "stop_market",
    }
    return mapping.get(order_type, order_type.lower())


def normalize_order_status(value: str | None) -> str:
    status = (value or "").upper()
    mapping = {
        "NEW": "working",
        "PARTIALLY_FILLED": "partially_filled",
        "FILLED": "filled",
        "CANCELED": "cancelled",
        "EXPIRED": "expired",
        "REJECTED": "rejected",
        "EXPIRED_IN_MATCH": "expired",
    }
    return mapping.get(status, status.lower())


def _as_levels(levels: list[list[str]]) -> list[OrderBookLevel]:
    result: list[OrderBookLevel] = []
    for level in levels:
        if len(level) < 2:
            continue
        result.append(OrderBookLevel(price=to_decimal(level[0]), size=to_decimal(level[1])))
    return result


def normalize_instrument(payload: dict[str, Any]) -> Instrument:
    filters = {str(item.get("filterType", "")): item for item in payload.get("filters", [])}
    price_filter = filters.get("PRICE_FILTER", {})
    lot_filter = filters.get("LOT_SIZE", {})
    min_notional_filter = filters.get("MIN_NOTIONAL", {})
    min_notional_raw = min_notional_filter.get("notional", min_notional_filter.get("minNotional"))
    status = str(payload.get("status", "TRADING")).lower()
    return Instrument(
        exchange_name=ExchangeName.BINANCE,
        symbol=payload["symbol"],
        market_type=MarketType.LINEAR_PERP,
        tick_size=to_decimal(price_filter.get("tickSize", "0")),
        lot_size=to_decimal(lot_filter.get("stepSize", "0")),
        min_quantity=to_decimal(lot_filter.get("minQty", "0")),
        min_notional=to_decimal(min_notional_raw) if min_notional_raw not in (None, "") else None,
        max_order_quantity=to_decimal(lot_filter.get("maxQty", "0")) if lot_filter else None,
        max_leverage=None,
        quote_asset=payload.get("quoteAsset", "USDT"),
        base_asset=payload.get("baseAsset", ""),
        status=status,
        price_scale=int(payload["pricePrecision"]) if payload.get("pricePrecision") is not None else None,
        raw_payload=payload,
    )


def normalize_account_snapshot(payload: dict[str, Any]) -> AccountState:
    wallet_balance = to_decimal(payload.get("totalWalletBalance", "0"))
    margin_balance = to_decimal(payload.get("totalMarginBalance", "0"))
    unrealized = to_decimal(payload.get("totalUnrealizedProfit", "0"))
    equity = margin_balance if margin_balance > 0 else wallet_balance + unrealized
    available = to_decimal(payload.get("availableBalance", payload.get("totalAvailableBalance", "0")))
    return AccountState(
        exchange_name=ExchangeName.BINANCE,
        equity=equity,
        available_balance=available,
        wallet_balance=wallet_balance,
        margin_balance=margin_balance,
        unrealized_pnl=unrealized,
        account_type="FUTURES_USDT",
        position_mode=PositionMode.ONE_WAY,
        raw_payload=payload,
    )


def normalize_position(payload: dict[str, Any]) -> PositionState:
    position_amt = to_decimal(payload.get("positionAmt", "0"))
    quantity = abs(position_amt)
    side = normalize_position_side(position_amt=position_amt, position_side=payload.get("positionSide"))
    updated_raw = payload.get("updateTime", payload.get("updateTimestamp", 0))
    updated_at = from_millis(updated_raw) if updated_raw not in (None, "", 0, "0") else datetime.now(timezone.utc)
    return PositionState(
        exchange_name=ExchangeName.BINANCE,
        symbol=payload["symbol"],
        side=side,
        quantity=quantity,
        entry_price=to_decimal(payload.get("entryPrice", "0")),
        mark_price=to_decimal(payload.get("markPrice")) if payload.get("markPrice") not in (None, "") else None,
        last_price=to_decimal(payload.get("markPrice")) if payload.get("markPrice") not in (None, "") else None,
        leverage=to_decimal(payload.get("leverage", "1")),
        realized_pnl=Decimal("0"),
        unrealized_pnl=to_decimal(payload.get("unRealizedProfit", payload.get("unrealizedProfit", "0"))),
        status="open" if quantity > 0 else "closed",
        raw_payload=payload,
        updated_at=updated_at,
    )


def normalize_order(payload: dict[str, Any]) -> OrderState:
    status = normalize_order_status(payload.get("status"))
    created_raw = payload.get("time", payload.get("createTime", payload.get("updateTime", 0)))
    updated_raw = payload.get("updateTime", created_raw)
    created_at = from_millis(created_raw) if created_raw not in (None, "", 0, "0") else datetime.now(timezone.utc)
    updated_at = from_millis(updated_raw) if updated_raw not in (None, "", 0, "0") else created_at
    price = payload.get("price")
    stop_price = payload.get("stopPrice")
    avg_price = payload.get("avgPrice")
    return OrderState(
        order_id=str(payload.get("orderId", payload.get("clientOrderId", ""))),
        exchange_name=ExchangeName.BINANCE,
        symbol=payload["symbol"],
        side=normalize_order_side(payload.get("side")),
        order_type=normalize_order_type(payload.get("type")),
        status=status,
        quantity=abs(to_decimal(payload.get("origQty", payload.get("q", "0")))),
        price=to_decimal(price) if price not in (None, "", "0", 0) else None,
        stop_price=to_decimal(stop_price) if stop_price not in (None, "", "0", 0) else None,
        reduce_only=bool(payload.get("reduceOnly", payload.get("R", False))),
        filled_quantity=abs(to_decimal(payload.get("executedQty", payload.get("z", "0")))),
        average_price=to_decimal(avg_price) if avg_price not in (None, "", "0", 0) else None,
        exchange_order_id=str(payload.get("orderId")) if payload.get("orderId") is not None else None,
        client_order_id=payload.get("clientOrderId", payload.get("c")),
        time_in_force=payload.get("timeInForce", payload.get("f")),
        raw_payload=payload,
        created_at=created_at,
        updated_at=updated_at,
    )


def normalize_public_message(message: dict[str, Any]) -> list[object]:
    event_type = str(message.get("e", ""))
    if event_type == "depthUpdate":
        return [
            OrderBookEvent(
                exchange_name=ExchangeName.BINANCE,
                symbol=message["s"],
                event_ts=from_millis(message.get("E", message.get("T", 0))),
                depth=len(message.get("b", [])),
                sequence=int(message["u"]) if message.get("u") is not None else None,
                update_id=int(message["u"]) if message.get("u") is not None else None,
                is_snapshot=False,
                bids=_as_levels(message.get("b", [])),
                asks=_as_levels(message.get("a", [])),
                raw_payload=message,
            )
        ]
    if event_type == "trade":
        is_buyer_maker = bool(message.get("m", False))
        side = "sell" if is_buyer_maker else "buy"
        return [
            TradeEvent(
                exchange_name=ExchangeName.BINANCE,
                symbol=message["s"],
                event_ts=from_millis(message.get("T", message.get("E", 0))),
                trade_id=str(message.get("t", "")),
                side=side,
                price=to_decimal(message.get("p", "0")),
                quantity=to_decimal(message.get("q", "0")),
                raw_payload=message,
            )
        ]
    if event_type == "bookTicker":
        return [
            TickerEvent(
                exchange_name=ExchangeName.BINANCE,
                symbol=message["s"],
                event_ts=from_millis(message.get("E", message.get("T", 0))),
                bid_price=to_decimal(message.get("b")) if message.get("b") not in (None, "") else None,
                ask_price=to_decimal(message.get("a")) if message.get("a") not in (None, "") else None,
                raw_payload=message,
            )
        ]
    if event_type == "markPriceUpdate":
        next_funding = message.get("T")
        return [
            FundingRateEvent(
                exchange_name=ExchangeName.BINANCE,
                symbol=message["s"],
                event_ts=from_millis(message.get("E", 0)),
                funding_rate=to_decimal(message.get("r", "0")),
                next_funding_at=from_millis(next_funding) if next_funding not in (None, "") else None,
                raw_payload=message,
            )
        ]
    if event_type == "kline" and isinstance(message.get("k"), dict):
        kline = message["k"]
        return [
            KlineEvent(
                exchange_name=ExchangeName.BINANCE,
                symbol=kline.get("s", message.get("s", "")),
                event_ts=from_millis(message.get("E", kline.get("T", 0))),
                interval=canonicalize_interval(kline.get("i", "1m")),
                start_at=from_millis(kline.get("t", 0)),
                end_at=from_millis(kline.get("T", 0)),
                open_price=to_decimal(kline.get("o", "0")),
                high_price=to_decimal(kline.get("h", "0")),
                low_price=to_decimal(kline.get("l", "0")),
                close_price=to_decimal(kline.get("c", "0")),
                volume=to_decimal(kline.get("v", "0")),
                turnover=to_decimal(kline.get("q", "0")),
                is_closed=bool(kline.get("x", False)),
                raw_payload=kline,
            )
        ]
    if event_type == "forceOrder" and isinstance(message.get("o"), dict):
        data = message["o"]
        return [
            LiquidationEvent(
                exchange_name=ExchangeName.BINANCE,
                symbol=data.get("s", message.get("s", "")),
                event_ts=from_millis(data.get("T", message.get("E", 0))),
                side=normalize_order_side(data.get("S", "")),
                price=to_decimal(data.get("p", "0")),
                quantity=to_decimal(data.get("q", "0")),
                raw_payload=data,
            )
        ]
    return []


def normalize_open_interest(symbol: str, payload: dict[str, Any]) -> OpenInterestEvent | None:
    if not payload:
        return None
    event_ts_raw = payload.get("time", payload.get("timestamp", 0))
    return OpenInterestEvent(
        exchange_name=ExchangeName.BINANCE,
        symbol=symbol,
        event_ts=from_millis(event_ts_raw) if event_ts_raw not in (None, "") else datetime.now(timezone.utc),
        open_interest=to_decimal(payload.get("openInterest", "0")),
        interval="5m",
        raw_payload=payload,
    )


def normalize_funding_rate(symbol: str, payload: dict[str, Any]) -> FundingRateEvent | None:
    if not payload:
        return None
    next_funding = payload.get("nextFundingTime")
    event_ts = payload.get("time", payload.get("nextFundingTime", 0))
    return FundingRateEvent(
        exchange_name=ExchangeName.BINANCE,
        symbol=symbol,
        event_ts=from_millis(event_ts) if event_ts not in (None, "") else datetime.now(timezone.utc),
        funding_rate=to_decimal(payload.get("lastFundingRate", "0")),
        next_funding_at=from_millis(next_funding) if next_funding not in (None, "") else None,
        raw_payload=payload,
    )


def normalize_rest_klines(symbol: str, *, interval: str | int, rows: list[Any]) -> list[KlineEvent]:
    canonical_interval = canonicalize_interval(interval)
    interval_minutes = interval_to_minutes(canonical_interval)
    events: list[KlineEvent] = []
    for row in rows:
        values = list(row) if not isinstance(row, dict) else []
        if isinstance(row, dict):
            start_raw = row.get("openTime")
            end_raw = row.get("closeTime")
            open_price = row.get("open")
            high_price = row.get("high")
            low_price = row.get("low")
            close_price = row.get("close")
            volume = row.get("volume")
            turnover = row.get("quoteVolume")
        else:
            if len(values) < 6:
                continue
            start_raw = values[0]
            end_raw = values[6] if len(values) > 6 else None
            open_price = values[1]
            high_price = values[2]
            low_price = values[3]
            close_price = values[4]
            volume = values[5]
            turnover = values[7] if len(values) > 7 else None
        if start_raw in (None, ""):
            continue
        start_at = from_millis(start_raw)
        end_at = from_millis(end_raw) if end_raw not in (None, "") else start_at + timedelta(minutes=interval_minutes)
        events.append(
            KlineEvent(
                exchange_name=ExchangeName.BINANCE,
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
    event_type = str(message.get("e", ""))
    event_ts = from_millis(message.get("E", 0)) if message.get("E") is not None else datetime.now(timezone.utc)
    events: list[object] = []
    if event_type == "ACCOUNT_UPDATE":
        account_data = message.get("a", {})
        balances = account_data.get("B", [])
        balance = next((row for row in balances if row.get("a") == "USDT"), balances[0] if balances else {})
        events.append(
            WalletEvent(
                exchange_name=ExchangeName.BINANCE,
                event_ts=event_ts,
                wallet_balance=to_decimal(balance.get("wb", "0")),
                available_balance=to_decimal(balance.get("cw", "0")),
                equity=to_decimal(balance.get("wb", "0")) + to_decimal(balance.get("bc", "0")),
                margin_balance=to_decimal(balance.get("wb", "0")),
                unrealized_pnl=to_decimal(account_data.get("m", "0")),
                account_type="FUTURES_USDT",
                raw_payload=message,
            )
        )
        for position_row in account_data.get("P", []):
            normalized_row = {
                "symbol": position_row.get("s", ""),
                "positionAmt": position_row.get("pa", "0"),
                "entryPrice": position_row.get("ep", "0"),
                "markPrice": position_row.get("mp", position_row.get("ep", "0")),
                "unRealizedProfit": position_row.get("up", "0"),
                "leverage": position_row.get("l", "1"),
                "positionSide": position_row.get("ps", "BOTH"),
                "updateTime": message.get("E", 0),
            }
            events.append(
                PositionUpdateEvent(
                    exchange_name=ExchangeName.BINANCE,
                    event_ts=event_ts,
                    position=normalize_position(normalized_row),
                    raw_payload=position_row,
                )
            )
    if event_type == "ORDER_TRADE_UPDATE":
        order_payload = message.get("o", {})
        normalized_order_payload = {
            "orderId": order_payload.get("i"),
            "clientOrderId": order_payload.get("c"),
            "symbol": order_payload.get("s", ""),
            "side": order_payload.get("S", ""),
            "type": order_payload.get("o", ""),
            "status": order_payload.get("X", ""),
            "origQty": order_payload.get("q", "0"),
            "executedQty": order_payload.get("z", "0"),
            "avgPrice": order_payload.get("ap", "0"),
            "price": order_payload.get("p", "0"),
            "stopPrice": order_payload.get("sp", "0"),
            "reduceOnly": order_payload.get("R", False),
            "timeInForce": order_payload.get("f"),
            "time": order_payload.get("T", message.get("E", 0)),
            "updateTime": order_payload.get("T", message.get("E", 0)),
        }
        events.append(
            OrderUpdateEvent(
                exchange_name=ExchangeName.BINANCE,
                event_ts=event_ts,
                order=normalize_order(normalized_order_payload),
                raw_payload=order_payload,
            )
        )
        if str(order_payload.get("x", "")).upper() == "TRADE" and to_decimal(order_payload.get("l", "0")) > 0:
            is_maker = bool(order_payload.get("m", False))
            events.append(
                ExecutionEvent(
                    exchange_name=ExchangeName.BINANCE,
                    event_ts=event_ts,
                    symbol=order_payload.get("s", ""),
                    order_id=str(order_payload.get("i", "")),
                    exchange_order_id=str(order_payload.get("i", "")),
                    exchange_fill_id=str(order_payload.get("t", "")) if order_payload.get("t") is not None else None,
                    side=normalize_order_side(order_payload.get("S", "")),
                    price=to_decimal(order_payload.get("L", "0")),
                    quantity=to_decimal(order_payload.get("l", "0")),
                    fee=to_decimal(order_payload.get("n", "0")),
                    liquidity_type="maker" if is_maker else "taker",
                    filled_at=from_millis(order_payload.get("T", message.get("E", 0))),
                    raw_payload=order_payload,
                )
            )
    return events
