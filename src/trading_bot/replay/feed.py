from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from decimal import Decimal

from trading_bot.domain.enums import ExchangeName, MarketType
from trading_bot.domain.models import Instrument
from trading_bot.marketdata.events import MarketEvent
from trading_bot.replay.reader import ReplayReader


class ReplayFeed:
    def __init__(self, *, reader: ReplayReader, strategy_start_at: datetime | None) -> None:
        self.reader = reader
        self.strategy_start_at = strategy_start_at
        self._events: list[MarketEvent] | None = None

    async def fetch_instruments(self, symbols: Sequence[str]) -> list[Instrument]:
        return [
            Instrument(
                exchange_name=ExchangeName.BYBIT,
                symbol=symbol,
                market_type=MarketType.LINEAR_PERP,
                tick_size=Decimal("0.01"),
                lot_size=Decimal("0.001"),
                min_quantity=Decimal("0.001"),
                quote_asset="USDT",
                base_asset=symbol.removesuffix("USDT"),
            )
            for symbol in symbols
        ]

    async def prime(self, symbols: Sequence[str]) -> list[MarketEvent]:
        await self._ensure_loaded(symbols=symbols)
        if self.strategy_start_at is None:
            return []
        return [event for event in self._events or [] if event.event_ts < self.strategy_start_at]

    async def stream(self, symbols: Sequence[str]) -> AsyncIterator[MarketEvent]:
        await self._ensure_loaded(symbols=symbols)
        for event in self._events or []:
            if self.strategy_start_at is not None and event.event_ts < self.strategy_start_at:
                continue
            yield event

    async def close(self) -> None:
        return None

    async def _ensure_loaded(self, *, symbols: Sequence[str]) -> None:
        if self._events is None:
            self._events = self.reader.read_events(symbols=list(symbols))
