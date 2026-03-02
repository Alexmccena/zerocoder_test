from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlencode

import httpx

from trading_bot.adapters.exchanges.bybit.capabilities import build_bybit_capabilities
from trading_bot.adapters.exchanges.bybit.normalizers import (
    normalize_account_snapshot,
    normalize_funding_rate,
    normalize_instrument,
    normalize_open_interest,
    normalize_order,
    normalize_position,
)
from trading_bot.config.schema import AppSettings
from trading_bot.domain.models import AccountState, ExchangeCapabilities, Instrument, OrderState, PositionState
from trading_bot.marketdata.events import FundingRateEvent, OpenInterestEvent
from trading_bot.observability.metrics import AppMetrics


class BybitRestClient:
    def __init__(
        self,
        *,
        config: AppSettings,
        api_key: str | None,
        api_secret: str | None,
        metrics: AppMetrics,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.config = config
        self.api_key = api_key
        self.api_secret = api_secret
        self.metrics = metrics
        self.base_url = "https://api-testnet.bybit.com" if config.exchange.testnet else "https://api.bybit.com"
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout_seconds)

    async def close(self) -> None:
        await self._client.aclose()

    def describe_capabilities(self) -> ExchangeCapabilities:
        return build_bybit_capabilities(self.config)

    def _sign(self, timestamp_ms: int, recv_window_ms: int, query_string: str) -> str:
        if self.api_secret is None:
            raise RuntimeError("Bybit private request requires TB_BYBIT_API_SECRET")
        payload = f"{timestamp_ms}{self.api_key or ''}{recv_window_ms}{query_string}"
        return hmac.new(self.api_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()

    async def _request(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        authenticated: bool = False,
    ) -> dict[str, Any]:
        query_string = urlencode({key: value for key, value in (params or {}).items() if value is not None})
        headers: dict[str, str] = {}
        if authenticated:
            if not self.api_key or not self.api_secret:
                raise RuntimeError("Bybit private request requires credentials")
            timestamp_ms = int(time.time() * 1000)
            recv_window_ms = self.config.exchange.recv_window_ms
            headers["X-BAPI-API-KEY"] = self.api_key
            headers["X-BAPI-TIMESTAMP"] = str(timestamp_ms)
            headers["X-BAPI-RECV-WINDOW"] = str(recv_window_ms)
            headers["X-BAPI-SIGN"] = self._sign(timestamp_ms, recv_window_ms, query_string)

        started_at = time.perf_counter()
        response = await self._client.get(path, params=params, headers=headers)
        seconds = time.perf_counter() - started_at
        self.metrics.record_bybit_rest_request(path, str(response.status_code), seconds)
        response.raise_for_status()
        payload = response.json()
        if int(payload.get("retCode", 0)) != 0:
            raise RuntimeError(f"Bybit API error for {path}: {payload.get('retMsg', 'unknown')}")
        return payload.get("result", {})

    async def fetch_instruments(self, symbols: Sequence[str] | None = None) -> list[Instrument]:
        result = await self._request("/v5/market/instruments-info", params={"category": "linear"})
        rows = result.get("list", [])
        instruments = [normalize_instrument(row) for row in rows]
        if symbols is None:
            return instruments
        allowlist = set(symbols)
        return [instrument for instrument in instruments if instrument.symbol in allowlist]

    async def fetch_recent_klines(self, symbol: str, *, interval: int, limit: int) -> list[dict[str, Any]]:
        result = await self._request(
            "/v5/market/kline",
            params={"category": "linear", "symbol": symbol, "interval": interval, "limit": limit},
        )
        return list(result.get("list", []))

    async def fetch_open_interest(self, symbol: str) -> OpenInterestEvent | None:
        result = await self._request(
            "/v5/market/open-interest",
            params={"category": "linear", "symbol": symbol, "intervalTime": "5min", "limit": 1},
        )
        return normalize_open_interest(symbol, result)

    async def fetch_funding_rate(self, symbol: str) -> FundingRateEvent | None:
        result = await self._request(
            "/v5/market/funding/history",
            params={"category": "linear", "symbol": symbol, "limit": 1},
        )
        return normalize_funding_rate(symbol, result)

    async def fetch_account_state(self) -> AccountState:
        result = await self._request(
            "/v5/account/wallet-balance",
            params={"accountType": "UNIFIED"},
            authenticated=True,
        )
        return normalize_account_snapshot(result)

    async def fetch_open_orders(self, symbol: str | None = None) -> list[OrderState]:
        result = await self._request(
            "/v5/order/realtime",
            params={"category": "linear", "symbol": symbol},
            authenticated=True,
        )
        return [normalize_order(row) for row in result.get("list", [])]

    async def fetch_positions(self) -> list[PositionState]:
        result = await self._request(
            "/v5/position/list",
            params={"category": "linear", "settleCoin": "USDT"},
            authenticated=True,
        )
        return [normalize_position(row) for row in result.get("list", [])]

    def build_private_ws_auth_message(self) -> str:
        if not self.api_key or not self.api_secret:
            raise RuntimeError("Bybit private websocket requires credentials")
        expires = int(time.time() * 1000) + 5000
        payload = f"GET/realtime{expires}"
        signature = hmac.new(self.api_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        return json.dumps({"op": "auth", "args": [self.api_key, expires, signature]})
