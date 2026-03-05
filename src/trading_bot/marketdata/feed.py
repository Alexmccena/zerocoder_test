from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

from trading_bot.marketdata.events import MarketEvent
from trading_bot.timeframes import canonicalize_interval, interval_to_bybit


class ExchangePublicMarketFeed:
    def __init__(
        self,
        *,
        rest_client: Any,
        public_ws_client: Any,
        public_message_normalizer: Callable[[dict[str, Any]], list[object]],
        interval_mapper: Callable[[str], str] | None = None,
    ) -> None:
        self.rest_client = rest_client
        self.public_ws_client = public_ws_client
        self.public_message_normalizer = public_message_normalizer
        self.interval_mapper = interval_mapper or (lambda interval: canonicalize_interval(interval))

    async def fetch_instruments(self, symbols: Sequence[str]) -> list:
        return await self.rest_client.fetch_instruments(symbols)

    async def prime(self, symbols: Sequence[str]) -> list[MarketEvent]:
        events: list[MarketEvent] = []
        for symbol in symbols:
            for interval in self.rest_client.config.market_data.kline_intervals:
                klines = await self.rest_client.fetch_recent_klines(
                    symbol,
                    interval=self.interval_mapper(interval),
                    limit=self.rest_client.config.market_data.bootstrap_kline_limit,
                )
                events.extend(klines)
            open_interest = await self.rest_client.fetch_open_interest(symbol)
            if open_interest is not None:
                events.append(open_interest)
            funding = await self.rest_client.fetch_funding_rate(symbol)
            if funding is not None:
                events.append(funding)
        events.sort(key=lambda event: (event.event_ts, event.event_type, event.symbol))
        return events

    async def stream(self, symbols: Sequence[str]) -> AsyncIterator[MarketEvent]:
        async for message in self.public_ws_client.stream(symbols):
            for event in self.public_message_normalizer(message):
                if isinstance(event, MarketEvent):
                    yield event

    async def close(self) -> None:
        await self.rest_client.close()


class BybitPublicMarketFeed(ExchangePublicMarketFeed):
    def __init__(self, *, rest_client: Any, public_ws_client: Any) -> None:
        from trading_bot.adapters.exchanges.bybit.normalizers import normalize_public_message

        super().__init__(
            rest_client=rest_client,
            public_ws_client=public_ws_client,
            public_message_normalizer=normalize_public_message,
            interval_mapper=interval_to_bybit,
        )
