from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

from trading_bot.adapters.exchanges.binance.rest import BinanceRestClient
from trading_bot.config.schema import AppSettings
from trading_bot.observability.metrics import AppMetrics


class BinancePrivateWebSocketClient:
    def __init__(
        self,
        *,
        config: AppSettings,
        rest_client: BinanceRestClient,
        metrics: AppMetrics,
    ) -> None:
        self.config = config
        self.rest_client = rest_client
        self.metrics = metrics
        self._base_url = (
            "wss://stream.binancefuture.com/ws"
            if config.exchange.testnet
            else "wss://fstream.binance.com/ws"
        )

    async def stream(self, *, on_connection_state_change=None) -> AsyncIterator[dict[str, Any]]:
        delay = self.config.market_data.ws_reconnect_min_seconds
        while True:
            try:
                async for item in self._stream_once(on_connection_state_change=on_connection_state_change):
                    delay = self.config.market_data.ws_reconnect_min_seconds
                    yield item
            except asyncio.CancelledError:
                if on_connection_state_change is not None:
                    maybe_awaitable = on_connection_state_change(False)
                    if asyncio.iscoroutine(maybe_awaitable):
                        await maybe_awaitable
                raise
            except Exception:
                if on_connection_state_change is not None:
                    maybe_awaitable = on_connection_state_change(False)
                    if asyncio.iscoroutine(maybe_awaitable):
                        await maybe_awaitable
                self.metrics.record_bybit_ws_reconnect("private")
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.config.market_data.ws_reconnect_max_seconds)

    async def _stream_once(self, *, on_connection_state_change=None) -> AsyncIterator[dict[str, Any]]:
        try:
            from websockets.asyncio.client import connect
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on local environment
            raise RuntimeError("websockets dependency is required for bot capture") from exc

        listen_key = await self.rest_client.start_user_stream()
        keepalive_task: asyncio.Task[None] | None = None
        try:
            async with connect(f"{self._base_url}/{listen_key}", max_size=None) as websocket:
                keepalive_task = asyncio.create_task(self._keepalive_loop(listen_key), name="binance-listenkey-keepalive")
                if on_connection_state_change is not None:
                    maybe_awaitable = on_connection_state_change(True)
                    if asyncio.iscoroutine(maybe_awaitable):
                        await maybe_awaitable
                async for raw in websocket:
                    message = json.loads(raw)
                    yield message
        finally:
            if keepalive_task is not None:
                keepalive_task.cancel()
                with suppress(asyncio.CancelledError):
                    await keepalive_task
            with suppress(Exception):
                await self.rest_client.close_user_stream(listen_key)

    async def _keepalive_loop(self, listen_key: str) -> None:
        while True:
            await asyncio.sleep(15 * 60)
            await self.rest_client.keepalive_user_stream(listen_key)

    async def probe_auth(self, *, timeout_seconds: float = 5.0) -> bool:
        del timeout_seconds
        try:
            from websockets.asyncio.client import connect
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on local environment
            raise RuntimeError("websockets dependency is required for bot capture") from exc

        listen_key = await self.rest_client.start_user_stream()
        try:
            async with connect(f"{self._base_url}/{listen_key}", max_size=None):
                return True
        finally:
            with suppress(Exception):
                await self.rest_client.close_user_stream(listen_key)
