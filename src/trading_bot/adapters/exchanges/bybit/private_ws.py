from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from trading_bot.adapters.exchanges.bybit.rest import BybitRestClient
from trading_bot.adapters.exchanges.bybit.topics import build_private_topics
from trading_bot.config.schema import AppSettings
from trading_bot.observability.metrics import AppMetrics


class BybitPrivateWebSocketClient:
    def __init__(self, *, config: AppSettings, rest_client: BybitRestClient, metrics: AppMetrics) -> None:
        self.config = config
        self.rest_client = rest_client
        self.metrics = metrics
        self.url = (
            "wss://stream-testnet.bybit.com/v5/private"
            if config.exchange.testnet
            else "wss://stream.bybit.com/v5/private"
        )

    async def stream(self) -> AsyncIterator[dict[str, Any]]:
        delay = self.config.market_data.ws_reconnect_min_seconds
        while True:
            try:
                async for item in self._stream_once():
                    delay = self.config.market_data.ws_reconnect_min_seconds
                    yield item
            except asyncio.CancelledError:
                raise
            except Exception:
                self.metrics.record_bybit_ws_reconnect("private")
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.config.market_data.ws_reconnect_max_seconds)

    async def _stream_once(self) -> AsyncIterator[dict[str, Any]]:
        try:
            from websockets.asyncio.client import connect
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on local environment
            raise RuntimeError("websockets dependency is required for bot capture") from exc

        async with connect(self.url, max_size=None) as websocket:
            await websocket.send(self.rest_client.build_private_ws_auth_message())
            await websocket.send(json.dumps({"op": "subscribe", "args": build_private_topics()}))
            async for raw in websocket:
                message = json.loads(raw)
                if message.get("op") == "ping":
                    await websocket.send(json.dumps({"op": "pong"}))
                    continue
                if message.get("op") in {"subscribe", "auth"} or message.get("success") is True:
                    continue
                yield message
