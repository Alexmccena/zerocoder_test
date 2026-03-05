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
    normalize_rest_klines,
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
        self._time_offset_ms = 0
        self._clock_synced = False

    async def close(self) -> None:
        await self._client.aclose()

    def describe_capabilities(self) -> ExchangeCapabilities:
        return build_bybit_capabilities(self.config)

    def _sign(self, timestamp_ms: int, recv_window_ms: int, query_string: str) -> str:
        if self.api_secret is None:
            raise RuntimeError("Bybit private request requires TB_BYBIT_API_SECRET")
        payload = f"{timestamp_ms}{self.api_key or ''}{recv_window_ms}{query_string}"
        return hmac.new(self.api_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()

    def _serialize_payload(self, payload: dict[str, Any] | None) -> str:
        if not payload:
            return ""
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)

    def _current_timestamp_ms(self) -> int:
        return int(time.time() * 1000) + self._time_offset_ms

    def _is_timestamp_error(self, payload: dict[str, Any]) -> bool:
        try:
            ret_code = int(payload.get("retCode", 0))
        except (TypeError, ValueError):
            ret_code = 0
        ret_msg = str(payload.get("retMsg", "")).lower()
        return ret_code == 10002 or "server timestamp" in ret_msg or "recv_window" in ret_msg

    async def _sync_time_offset(self, *, force: bool = False) -> None:
        if self._clock_synced and not force:
            return
        result = await self._request("/v5/market/time")
        time_nano = result.get("timeNano")
        time_second = result.get("timeSecond")
        if time_nano is not None:
            server_ms = int(str(time_nano)) // 1_000_000
        elif time_second is not None:
            server_ms = int(str(time_second)) * 1000
        else:
            raise RuntimeError("Bybit API error for /v5/market/time: missing server time")
        local_ms = int(time.time() * 1000)
        self._time_offset_ms = server_ms - local_ms - 250
        self._clock_synced = True

    async def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        authenticated: bool = False,
    ) -> dict[str, Any]:
        method_upper = method.upper()
        request_params = {key: value for key, value in (params or {}).items() if value is not None}
        query_string = urlencode(request_params)
        body_string = self._serialize_payload(payload)
        if authenticated:
            if not self.api_key or not self.api_secret:
                raise RuntimeError("Bybit private request requires credentials")
            try:
                await self._sync_time_offset()
            except Exception:
                # Keep optimistic path when market-time probe fails; retry handles timestamp errors.
                pass

        max_attempts = 2 if authenticated else 1
        for attempt in range(max_attempts):
            headers: dict[str, str] = {}
            if authenticated:
                timestamp_ms = self._current_timestamp_ms()
                recv_window_ms = self.config.exchange.recv_window_ms
                headers["X-BAPI-API-KEY"] = self.api_key or ""
                headers["X-BAPI-TIMESTAMP"] = str(timestamp_ms)
                headers["X-BAPI-RECV-WINDOW"] = str(recv_window_ms)
                headers["X-BAPI-SIGN"] = self._sign(
                    timestamp_ms,
                    recv_window_ms,
                    query_string if method_upper == "GET" else body_string,
                )
            if method_upper != "GET":
                headers["Content-Type"] = "application/json"

            started_at = time.perf_counter()
            try:
                response = await self._client.request(
                    method_upper,
                    path,
                    params=request_params if method_upper == "GET" else None,
                    content=body_string if method_upper != "GET" else None,
                    headers=headers,
                )
                seconds = time.perf_counter() - started_at
                self.metrics.record_bybit_rest_request(path, str(response.status_code), seconds)
                response.raise_for_status()
            except httpx.TimeoutException:
                seconds = time.perf_counter() - started_at
                self.metrics.record_bybit_rest_request(path, "timeout", seconds)
                raise
            except httpx.HTTPStatusError:
                raise

            response_payload = response.json()
            if int(response_payload.get("retCode", 0)) == 0:
                return response_payload.get("result", {})

            if authenticated and attempt == 0 and self._is_timestamp_error(response_payload):
                await self._sync_time_offset(force=True)
                continue

            raise RuntimeError(f"Bybit API error for {path}: {response_payload.get('retMsg', 'unknown')}")

        raise RuntimeError(f"Bybit API error for {path}: exhausted retries")

    async def fetch_instruments(self, symbols: Sequence[str] | None = None) -> list[Instrument]:
        result = await self._request("/v5/market/instruments-info", params={"category": "linear"})
        rows = result.get("list", [])
        instruments = [normalize_instrument(row) for row in rows]
        if symbols is None:
            return instruments
        allowlist = set(symbols)
        return [instrument for instrument in instruments if instrument.symbol in allowlist]

    async def fetch_recent_klines(self, symbol: str, *, interval: str | int, limit: int) -> list:
        result = await self._request(
            "/v5/market/kline",
            params={"category": "linear", "symbol": symbol, "interval": interval, "limit": limit},
        )
        return normalize_rest_klines(symbol, interval=interval, rows=list(result.get("list", [])))

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
        params: dict[str, Any] = {"category": "linear"}
        if symbol is not None:
            params["symbol"] = symbol
        else:
            params["settleCoin"] = "USDT"
        result = await self._request(
            "/v5/order/realtime",
            params=params,
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

    async def create_order(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str,
        quantity: str,
        client_order_id: str,
        price: str | None = None,
        trigger_price: str | None = None,
        reduce_only: bool = False,
        close_on_trigger: bool = False,
        time_in_force: str | None = None,
        position_idx: int = 0,
        trigger_direction: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "category": "linear",
            "symbol": symbol,
            "side": "Buy" if side.lower() == "buy" else "Sell",
            "orderType": "Market" if order_type.lower() == "market" else "Limit",
            "qty": quantity,
            "orderLinkId": client_order_id,
            "reduceOnly": bool(reduce_only),
            "closeOnTrigger": bool(close_on_trigger),
            "positionIdx": position_idx,
        }
        if price is not None:
            payload["price"] = price
        if trigger_price is not None:
            payload["triggerPrice"] = trigger_price
        if time_in_force is not None:
            payload["timeInForce"] = time_in_force
        if trigger_direction is not None:
            payload["triggerDirection"] = trigger_direction
        return await self._request(
            "/v5/order/create",
            method="POST",
            payload=payload,
            authenticated=True,
        )

    async def cancel_order(
        self,
        *,
        symbol: str,
        exchange_order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        if exchange_order_id is None and client_order_id is None:
            raise ValueError("Either exchange_order_id or client_order_id must be provided for cancel_order")
        payload: dict[str, Any] = {
            "category": "linear",
            "symbol": symbol,
        }
        if exchange_order_id is not None:
            payload["orderId"] = exchange_order_id
        if client_order_id is not None:
            payload["orderLinkId"] = client_order_id
        return await self._request(
            "/v5/order/cancel",
            method="POST",
            payload=payload,
            authenticated=True,
        )

    async def fetch_order(
        self,
        *,
        symbol: str | None = None,
        exchange_order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> OrderState | None:
        if exchange_order_id is None and client_order_id is None:
            raise ValueError("Either exchange_order_id or client_order_id must be provided for fetch_order")
        params: dict[str, Any] = {
            "category": "linear",
            "symbol": symbol,
            "orderId": exchange_order_id,
            "orderLinkId": client_order_id,
        }
        result = await self._request(
            "/v5/order/realtime",
            params=params,
            authenticated=True,
        )
        rows = list(result.get("list", []))
        if not rows:
            return None
        return normalize_order(rows[0])

    def build_private_ws_auth_message(self) -> str:
        if not self.api_key or not self.api_secret:
            raise RuntimeError("Bybit private websocket requires credentials")
        expires = int(time.time() * 1000) + 5000
        payload = f"GET/realtime{expires}"
        signature = hmac.new(self.api_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        return json.dumps({"op": "auth", "args": [self.api_key, expires, signature]})
