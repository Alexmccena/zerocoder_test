from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from trading_bot.adapters.exchanges.bybit.normalizers import normalize_public_message
from trading_bot.adapters.exchanges.bybit.public_ws import BybitPublicWebSocketClient
from trading_bot.adapters.exchanges.bybit.rest import BybitRestClient
from trading_bot.marketdata.events import MarketEvent


class BybitPublicMarketFeed:
    def __init__(
        self,
        *,
        rest_client: BybitRestClient,
        public_ws_client: BybitPublicWebSocketClient,
    ) -> None:
        self.rest_client = rest_client
        self.public_ws_client = public_ws_client

    async def fetch_instruments(self, symbols: Sequence[str]) -> list:
        return await self.rest_client.fetch_instruments(symbols)

    async def prime(self, symbols: Sequence[str]) -> list[MarketEvent]:
        events: list[MarketEvent] = []
        for symbol in symbols:
            open_interest = await self.rest_client.fetch_open_interest(symbol)
            if open_interest is not None:
                events.append(open_interest)
            funding = await self.rest_client.fetch_funding_rate(symbol)
            if funding is not None:
                events.append(funding)
        return events

    async def stream(self, symbols: Sequence[str]) -> AsyncIterator[MarketEvent]:
        async for message in self.public_ws_client.stream(symbols):
            for event in normalize_public_message(message):
                if isinstance(event, MarketEvent):
                    yield event

    async def close(self) -> None:
        await self.rest_client.close()
