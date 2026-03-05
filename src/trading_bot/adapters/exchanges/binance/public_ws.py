from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

from trading_bot.adapters.exchanges.binance.topics import build_public_topics
from trading_bot.config.schema import AppSettings
from trading_bot.observability.metrics import AppMetrics


class BinancePublicWebSocketClient:
    def __init__(self, *, config: AppSettings, metrics: AppMetrics) -> None:
        self.config = config
        self.metrics = metrics
        self.url = (
            "wss://stream.binancefuture.com/ws"
            if config.exchange.testnet
            else "wss://fstream.binance.com/ws"
        )

    async def stream(self, symbols: Sequence[str]) -> AsyncIterator[dict[str, Any]]:
        topics = build_public_topics(symbols, self.config.market_data)
        delay = self.config.market_data.ws_reconnect_min_seconds
        while True:
            try:
                async for item in self._stream_once(topics):
                    delay = self.config.market_data.ws_reconnect_min_seconds
                    yield item
            except asyncio.CancelledError:
                raise
            except Exception:
                self.metrics.record_bybit_ws_reconnect("public")
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.config.market_data.ws_reconnect_max_seconds)

    async def _stream_once(self, topics: list[str]) -> AsyncIterator[dict[str, Any]]:
        try:
            from websockets.asyncio.client import connect
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on local environment
            raise RuntimeError("websockets dependency is required for bot capture") from exc

        async with connect(self.url, max_size=None) as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "method": "SUBSCRIBE",
                        "params": topics,
                        "id": 1,
                    }
                )
            )
            async for raw in websocket:
                message = json.loads(raw)
                if "result" in message and message.get("id") == 1:
                    continue
                yield message
