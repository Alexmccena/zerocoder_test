from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from trading_bot.domain.models import Instrument, MarketSnapshot
from trading_bot.features.engine import FeatureProvider
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
