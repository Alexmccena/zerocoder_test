from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlencode

import httpx

from trading_bot.adapters.exchanges.binance.capabilities import build_binance_capabilities
from trading_bot.adapters.exchanges.binance.normalizers import (
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


class BinanceRestClient:
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
        self.base_url = (
            "https://testnet.binancefuture.com"
            if config.exchange.testnet
            else "https://fapi.binance.com"
        )
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout_seconds)
        self._time_offset_ms = 0
        self._clock_synced = False

    async def close(self) -> None:
        await self._client.aclose()

    def describe_capabilities(self) -> ExchangeCapabilities:
        return build_binance_capabilities(self.config)

    def _current_timestamp_ms(self) -> int:
        return int(time.time() * 1000) + self._time_offset_ms

    def _sign(self, query_string: str) -> str:
        if not self.api_secret:
            raise RuntimeError("Binance private request requires TB_BINANCE_API_SECRET")
        digest = hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return digest

    async def _sync_time_offset(self, *, force: bool = False) -> None:
        if self._clock_synced and not force:
            return
        payload = await self._request("/fapi/v1/time")
        server_ms = int(payload.get("serverTime", 0))
        if server_ms <= 0:
            raise RuntimeError("Binance API error for /fapi/v1/time: missing serverTime")
        local_ms = int(time.time() * 1000)
        self._time_offset_ms = server_ms - local_ms - 250
        self._clock_synced = True

    def _is_timestamp_error(self, payload: dict[str, Any]) -> bool:
        code = int(payload.get("code", 0)) if payload.get("code") is not None else 0
        message = str(payload.get("msg", "")).lower()
        return code in {-1021, -1022} or "timestamp" in message

    async def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        params: dict[str, Any] | None = None,
        authenticated: bool = False,
        api_key_only: bool = False,
    ) -> dict[str, Any]:
        method_upper = method.upper()
        request_params = {key: value for key, value in (params or {}).items() if value is not None}

        if authenticated:
            if not self.api_key or not self.api_secret:
                raise RuntimeError("Binance private request requires credentials")
            await self._sync_time_offset()
            request_params["recvWindow"] = self.config.exchange.recv_window_ms
            request_params["timestamp"] = self._current_timestamp_ms()

        query_string = urlencode(request_params, doseq=True)
        if authenticated:
            request_params["signature"] = self._sign(query_string)

        headers: dict[str, str] = {}
        if authenticated or api_key_only:
            if not self.api_key:
                raise RuntimeError("Binance private request requires TB_BINANCE_API_KEY")
            headers["X-MBX-APIKEY"] = self.api_key

        max_attempts = 2 if authenticated else 1
        for attempt in range(max_attempts):
            started_at = time.perf_counter()
            try:
                response = await self._client.request(
                    method_upper,
                    path,
                    params=request_params,
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
                try:
                    payload = response.json()
                except Exception:
                    raise
                if authenticated and attempt == 0 and self._is_timestamp_error(payload):
                    await self._sync_time_offset(force=True)
                    request_params["timestamp"] = self._current_timestamp_ms()
                    query_string = urlencode(
                        {key: value for key, value in request_params.items() if key != "signature"},
                        doseq=True,
                    )
                    request_params["signature"] = self._sign(query_string)
                    continue
                raise RuntimeError(f"Binance API error for {path}: {payload.get('msg', 'unknown')}")

            payload = response.json()
            if isinstance(payload, dict) and payload.get("code") not in (None, 0):
                if authenticated and attempt == 0 and self._is_timestamp_error(payload):
                    await self._sync_time_offset(force=True)
                    request_params["timestamp"] = self._current_timestamp_ms()
                    query_string = urlencode(
                        {key: value for key, value in request_params.items() if key != "signature"},
                        doseq=True,
                    )
                    request_params["signature"] = self._sign(query_string)
                    continue
                raise RuntimeError(f"Binance API error for {path}: {payload.get('msg', 'unknown')}")
            return payload

        raise RuntimeError(f"Binance API error for {path}: exhausted retries")

    async def fetch_instruments(self, symbols: Sequence[str] | None = None) -> list[Instrument]:
        payload = await self._request("/fapi/v1/exchangeInfo")
        rows = payload.get("symbols", [])
        instruments: list[Instrument] = []
        for row in rows:
            if row.get("contractType") != "PERPETUAL":
                continue
            if row.get("quoteAsset") != "USDT":
                continue
            instruments.append(normalize_instrument(row))
        if symbols is None:
            return instruments
        allowlist = set(symbols)
        return [instrument for instrument in instruments if instrument.symbol in allowlist]

    async def fetch_recent_klines(self, symbol: str, *, interval: str | int, limit: int) -> list:
        rows = await self._request(
            "/fapi/v1/klines",
            params={"symbol": symbol, "interval": str(interval), "limit": limit},
        )
        return normalize_rest_klines(symbol, interval=interval, rows=list(rows))

    async def fetch_open_interest(self, symbol: str) -> OpenInterestEvent | None:
        payload = await self._request(
            "/fapi/v1/openInterest",
            params={"symbol": symbol},
        )
        return normalize_open_interest(symbol, payload)

    async def fetch_funding_rate(self, symbol: str) -> FundingRateEvent | None:
        payload = await self._request(
            "/fapi/v1/premiumIndex",
            params={"symbol": symbol},
        )
        return normalize_funding_rate(symbol, payload)

    async def fetch_account_state(self) -> AccountState:
        payload = await self._request("/fapi/v2/account", authenticated=True)
        return normalize_account_snapshot(payload)

    async def fetch_open_orders(self, symbol: str | None = None) -> list[OrderState]:
        params: dict[str, Any] = {}
        if symbol is not None:
            params["symbol"] = symbol
        rows = await self._request("/fapi/v1/openOrders", params=params, authenticated=True)
        return [normalize_order(row) for row in rows]

    async def fetch_positions(self) -> list[PositionState]:
        rows = await self._request("/fapi/v2/positionRisk", authenticated=True)
        return [normalize_position(row) for row in rows]

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
        del position_idx, trigger_direction  # Binance futures does not use these fields in one-way mode.
        payload: dict[str, Any] = {
            "symbol": symbol,
            "side": "BUY" if side.lower() == "buy" else "SELL",
            "newClientOrderId": client_order_id,
            "reduceOnly": "true" if reduce_only else "false",
            "newOrderRespType": "ACK",
        }
        normalized_type = order_type.lower()
        if normalized_type == "market":
            payload["type"] = "MARKET"
            payload["quantity"] = quantity
        elif normalized_type == "limit":
            payload["type"] = "LIMIT"
            payload["quantity"] = quantity
            payload["price"] = price
            payload["timeInForce"] = time_in_force or "GTC"
        elif normalized_type == "stop_market":
            payload["type"] = "STOP_MARKET"
            payload["stopPrice"] = trigger_price
            payload["workingType"] = "MARK_PRICE"
            if close_on_trigger:
                payload["closePosition"] = "true"
            else:
                payload["quantity"] = quantity
        else:
            raise ValueError(f"Unsupported order_type for Binance: {order_type}")
        return await self._request(
            "/fapi/v1/order",
            method="POST",
            params=payload,
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
        payload: dict[str, Any] = {"symbol": symbol}
        if exchange_order_id is not None:
            payload["orderId"] = exchange_order_id
        if client_order_id is not None:
            payload["origClientOrderId"] = client_order_id
        return await self._request(
            "/fapi/v1/order",
            method="DELETE",
            params=payload,
            authenticated=True,
        )

    async def fetch_order(
        self,
        *,
        symbol: str | None = None,
        exchange_order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> OrderState | None:
        if symbol is None:
            raise ValueError("Binance fetch_order requires symbol")
        if exchange_order_id is None and client_order_id is None:
            raise ValueError("Either exchange_order_id or client_order_id must be provided for fetch_order")
        params: dict[str, Any] = {"symbol": symbol}
        if exchange_order_id is not None:
            params["orderId"] = exchange_order_id
        if client_order_id is not None:
            params["origClientOrderId"] = client_order_id
        payload = await self._request("/fapi/v1/order", params=params, authenticated=True)
        if not payload:
            return None
        return normalize_order(payload)

    async def start_user_stream(self) -> str:
        payload = await self._request("/fapi/v1/listenKey", method="POST", api_key_only=True)
        listen_key = payload.get("listenKey")
        if not isinstance(listen_key, str) or not listen_key:
            raise RuntimeError("Binance API error: missing listenKey")
        return listen_key

    async def keepalive_user_stream(self, listen_key: str) -> None:
        await self._request(
            "/fapi/v1/listenKey",
            method="PUT",
            params={"listenKey": listen_key},
            api_key_only=True,
        )

    async def close_user_stream(self, listen_key: str) -> None:
        await self._request(
            "/fapi/v1/listenKey",
            method="DELETE",
            params={"listenKey": listen_key},
            api_key_only=True,
        )
