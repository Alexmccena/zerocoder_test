from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from trading_bot.domain.models import FeatureSnapshot, Instrument, MarketSnapshot
from trading_bot.marketdata.events import (
    FundingRateEvent,
    KlineEvent,
    MarketEvent,
    OpenInterestEvent,
    OrderBookEvent,
    TickerEvent,
    TradeEvent,
)


@dataclass(slots=True)
class _SymbolState:
    instrument: Instrument | None = None
    orderbook: OrderBookEvent | None = None
    ticker: TickerEvent | None = None
    last_trade: TradeEvent | None = None
    closed_klines_by_interval: dict[str, KlineEvent] = field(default_factory=dict)
    open_interest: OpenInterestEvent | None = None
    funding_rate: FundingRateEvent | None = None
    last_event_ts: datetime | None = None


class MarketSnapshotBuilder:
    def __init__(self, *, stale_after_seconds: int) -> None:
        self.stale_after_seconds = stale_after_seconds
        self._state: dict[str, _SymbolState] = defaultdict(_SymbolState)

    def register_instruments(self, instruments: list[Instrument]) -> None:
        for instrument in instruments:
            self._state[instrument.symbol].instrument = instrument

    def apply_event(self, event: MarketEvent) -> None:
        state = self._state[event.symbol]
        state.last_event_ts = event.event_ts
        if isinstance(event, OrderBookEvent):
            state.orderbook = event
        elif isinstance(event, TickerEvent):
            state.ticker = event
        elif isinstance(event, TradeEvent):
            state.last_trade = event
        elif isinstance(event, KlineEvent) and event.is_closed:
            state.closed_klines_by_interval[event.interval] = event
        elif isinstance(event, OpenInterestEvent):
            state.open_interest = event
        elif isinstance(event, FundingRateEvent):
            state.funding_rate = event

    def build(self, symbol: str, *, as_of: datetime) -> MarketSnapshot:
        state = self._state[symbol]
        data_is_stale = True
        if state.orderbook is not None:
            data_is_stale = (as_of - state.orderbook.event_ts).total_seconds() > self.stale_after_seconds
        return MarketSnapshot(
            symbol=symbol,
            as_of=as_of,
            instrument=state.instrument,
            orderbook=state.orderbook,
            ticker=state.ticker,
            last_trade=state.last_trade,
            closed_klines_by_interval=dict(state.closed_klines_by_interval),
            open_interest=state.open_interest,
            funding_rate=state.funding_rate,
            data_is_stale=data_is_stale,
        )


class FeatureProvider:
    def __init__(self, *, timeframe: str) -> None:
        self.timeframe = timeframe
        self._last_seen_kline_end: dict[str, datetime] = {}
        self._previous_close: dict[str, Decimal] = {}
        self._last_seen_open_interest: dict[str, Decimal] = {}

    def compute(self, snapshot: MarketSnapshot) -> FeatureSnapshot:
        latest_kline = snapshot.closed_klines_by_interval.get(self.timeframe)
        last_close_change_bps = Decimal("0")
        if latest_kline is not None and self._last_seen_kline_end.get(snapshot.symbol) != latest_kline.end_at:
            previous_close = self._previous_close.get(snapshot.symbol)
            if previous_close not in (None, Decimal("0")):
                last_close_change_bps = ((latest_kline.close_price - previous_close) / previous_close) * Decimal(
                    "10000"
                )
            self._previous_close[snapshot.symbol] = latest_kline.close_price
            self._last_seen_kline_end[snapshot.symbol] = latest_kline.end_at

        open_interest_delta = Decimal("0")
        if snapshot.open_interest is not None:
            previous_oi = self._last_seen_open_interest.get(snapshot.symbol)
            if previous_oi is not None:
                open_interest_delta = snapshot.open_interest.open_interest - previous_oi
            self._last_seen_open_interest[snapshot.symbol] = snapshot.open_interest.open_interest

        top5_bid = Decimal("0")
        top5_ask = Decimal("0")
        if snapshot.orderbook is not None:
            top5_bid = sum((level.size for level in snapshot.orderbook.bids[:5]), start=Decimal("0"))
            top5_ask = sum((level.size for level in snapshot.orderbook.asks[:5]), start=Decimal("0"))
        denominator = top5_bid + top5_ask
        top5_imbalance = float((top5_bid - top5_ask) / denominator) if denominator != 0 else 0.0
        funding_rate = snapshot.funding_rate.funding_rate if snapshot.funding_rate is not None else Decimal("0")

        return FeatureSnapshot(
            symbol=snapshot.symbol,
            last_close_change_bps=last_close_change_bps,
            top5_imbalance=top5_imbalance,
            open_interest_delta=open_interest_delta,
            funding_rate=funding_rate,
            has_fresh_orderbook=not snapshot.data_is_stale and snapshot.orderbook is not None,
            payload={"timeframe": self.timeframe},
        )
