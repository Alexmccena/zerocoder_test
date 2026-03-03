from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field

from trading_bot.domain.enums import ExchangeName
from trading_bot.domain.models import DomainModel, MarketSnapshot, OrderState, PositionState, utc_now


class EventEnvelope(DomainModel):
    exchange_name: ExchangeName
    event_ts: datetime
    received_at: datetime = Field(default_factory=utc_now)
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class MarketEvent(EventEnvelope):
    event_type: str
    symbol: str


class PrivateStateEvent(EventEnvelope):
    event_type: str


class OrderBookLevel(DomainModel):
    price: Decimal
    size: Decimal


class OrderBookEvent(MarketEvent):
    event_type: Literal["orderbook"] = "orderbook"
    depth: int
    sequence: int | None = None
    update_id: int | None = None
    is_snapshot: bool = False
    bids: list[OrderBookLevel] = Field(default_factory=list)
    asks: list[OrderBookLevel] = Field(default_factory=list)


class TradeEvent(MarketEvent):
    event_type: Literal["trade"] = "trade"
    trade_id: str | None = None
    side: str
    price: Decimal
    quantity: Decimal


class TickerEvent(MarketEvent):
    event_type: Literal["ticker"] = "ticker"
    bid_price: Decimal | None = None
    ask_price: Decimal | None = None
    last_price: Decimal | None = None
    mark_price: Decimal | None = None
    index_price: Decimal | None = None
    open_interest: Decimal | None = None
    funding_rate: Decimal | None = None


class KlineEvent(MarketEvent):
    event_type: Literal["kline"] = "kline"
    interval: str
    start_at: datetime
    end_at: datetime
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: Decimal
    turnover: Decimal | None = None
    is_closed: bool = False


class LiquidationEvent(MarketEvent):
    event_type: Literal["liquidation"] = "liquidation"
    side: str
    price: Decimal
    quantity: Decimal


class OpenInterestEvent(MarketEvent):
    event_type: Literal["open_interest"] = "open_interest"
    open_interest: Decimal
    interval: str | None = None


class FundingRateEvent(MarketEvent):
    event_type: Literal["funding_rate"] = "funding_rate"
    funding_rate: Decimal
    next_funding_at: datetime | None = None


class WalletEvent(PrivateStateEvent):
    event_type: Literal["wallet"] = "wallet"
    wallet_balance: Decimal
    available_balance: Decimal
    equity: Decimal
    margin_balance: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    account_type: str = "UNIFIED"


class OrderUpdateEvent(PrivateStateEvent):
    event_type: Literal["order"] = "order"
    order: OrderState


class ExecutionEvent(PrivateStateEvent):
    event_type: Literal["execution"] = "execution"
    symbol: str
    order_id: str
    exchange_order_id: str | None = None
    exchange_fill_id: str | None = None
    side: str
    price: Decimal
    quantity: Decimal
    fee: Decimal = Decimal("0")
    liquidity_type: str = "unknown"
    filled_at: datetime


class PositionUpdateEvent(PrivateStateEvent):
    event_type: Literal["position"] = "position"
    position: PositionState


MarketSnapshot.model_rebuild(
    _types_namespace={
        "FundingRateEvent": FundingRateEvent,
        "KlineEvent": KlineEvent,
        "OpenInterestEvent": OpenInterestEvent,
        "OrderBookEvent": OrderBookEvent,
        "TickerEvent": TickerEvent,
        "TradeEvent": TradeEvent,
    }
)
