from __future__ import annotations

from urllib.parse import parse_qs

import httpx

from trading_bot.adapters.exchanges.bybit.rest import BybitRestClient
from trading_bot.config.schema import AppSettings
from trading_bot.observability.metrics import AppMetrics


def _build_settings(*, testnet: bool) -> AppSettings:
    return AppSettings.model_validate(
        {
            "runtime": {
                "service_name": "trading-bot",
                "mode": "live",
                "environment": "prod",
            },
            "exchange": {
                "primary": "bybit",
                "market_type": "linear_perp",
                "position_mode": "one_way",
                "account_alias": "default",
                "testnet": testnet,
                "private_state_enabled": True,
                "recv_window_ms": 5000,
            },
            "symbols": {"allowlist": ["BTCUSDT"]},
            "storage": {
                "postgres_dsn": "postgresql+asyncpg://user:pass@localhost:5432/app",
                "redis_dsn": "redis://localhost:6379/0",
            },
            "observability": {"log_level": "INFO", "http_host": "127.0.0.1", "http_port": 8080},
            "risk": {
                "max_open_positions": 2,
                "risk_per_trade": 0.01,
                "max_daily_loss": 0.02,
            },
            "llm": {"enabled": False, "provider": "none", "model_name": "", "timeout_seconds": 10},
        }
    )


async def test_private_request_uses_server_time_probe_before_signing() -> None:
    settings = _build_settings(testnet=False)
    call_order: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        call_order.append(request.url.path)
        if request.url.path == "/v5/market/time":
            return httpx.Response(
                status_code=200,
                json={"retCode": 0, "retMsg": "OK", "result": {"timeSecond": "1700000000"}},
            )
        if request.url.path == "/v5/account/wallet-balance":
            assert "X-BAPI-TIMESTAMP" in request.headers
            assert "X-BAPI-SIGN" in request.headers
            return httpx.Response(
                status_code=200,
                json={"retCode": 0, "retMsg": "OK", "result": {"accountType": "UNIFIED"}},
            )
        return httpx.Response(status_code=404, json={"retCode": 1, "retMsg": "not found", "result": {}})

    client = BybitRestClient(
        config=settings,
        api_key="key",
        api_secret="secret",
        metrics=AppMetrics(),
    )
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        base_url=client.base_url,
        transport=httpx.MockTransport(handler),
    )

    try:
        result = await client._request(
            "/v5/account/wallet-balance",
            params={"accountType": "UNIFIED"},
            authenticated=True,
        )
    finally:
        await client.close()

    assert result == {"accountType": "UNIFIED"}
    assert call_order == ["/v5/market/time", "/v5/account/wallet-balance"]


async def test_private_request_retries_once_after_timestamp_error() -> None:
    settings = _build_settings(testnet=False)
    call_order: list[str] = []
    wallet_calls = 0
    time_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal wallet_calls, time_calls
        call_order.append(request.url.path)
        if request.url.path == "/v5/market/time":
            time_calls += 1
            return httpx.Response(
                status_code=200,
                json={
                    "retCode": 0,
                    "retMsg": "OK",
                    "result": {"timeSecond": str(1700000000 + time_calls)},
                },
            )
        if request.url.path == "/v5/account/wallet-balance":
            wallet_calls += 1
            if wallet_calls == 1:
                return httpx.Response(
                    status_code=200,
                    json={
                        "retCode": 10002,
                        "retMsg": "invalid request, please check your server timestamp or recv_window param",
                        "result": {},
                    },
                )
            return httpx.Response(
                status_code=200,
                json={"retCode": 0, "retMsg": "OK", "result": {"status": "ok"}},
            )
        return httpx.Response(status_code=404, json={"retCode": 1, "retMsg": "not found", "result": {}})

    client = BybitRestClient(
        config=settings,
        api_key="key",
        api_secret="secret",
        metrics=AppMetrics(),
    )
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        base_url=client.base_url,
        transport=httpx.MockTransport(handler),
    )

    try:
        result = await client._request(
            "/v5/account/wallet-balance",
            params={"accountType": "UNIFIED"},
            authenticated=True,
        )
    finally:
        await client.close()

    assert result == {"status": "ok"}
    assert call_order == [
        "/v5/market/time",
        "/v5/account/wallet-balance",
        "/v5/market/time",
        "/v5/account/wallet-balance",
    ]


async def test_private_request_omits_none_query_params() -> None:
    settings = _build_settings(testnet=False)
    observed_query = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed_query
        if request.url.path == "/v5/market/time":
            return httpx.Response(
                status_code=200,
                json={"retCode": 0, "retMsg": "OK", "result": {"timeSecond": "1700000000"}},
            )
        if request.url.path == "/v5/order/realtime":
            observed_query = request.url.query.decode("utf-8")
            return httpx.Response(
                status_code=200,
                json={"retCode": 0, "retMsg": "OK", "result": {"list": []}},
            )
        return httpx.Response(status_code=404, json={"retCode": 1, "retMsg": "not found", "result": {}})

    client = BybitRestClient(
        config=settings,
        api_key="key",
        api_secret="secret",
        metrics=AppMetrics(),
    )
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        base_url=client.base_url,
        transport=httpx.MockTransport(handler),
    )

    try:
        result = await client._request(
            "/v5/order/realtime",
            params={"category": "linear", "symbol": None},
            authenticated=True,
        )
    finally:
        await client.close()

    assert result == {"list": []}
    assert observed_query == "category=linear"


async def test_fetch_open_orders_defaults_to_settle_coin_when_symbol_missing() -> None:
    settings = _build_settings(testnet=False)
    observed_query = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed_query
        if request.url.path == "/v5/market/time":
            return httpx.Response(
                status_code=200,
                json={"retCode": 0, "retMsg": "OK", "result": {"timeSecond": "1700000000"}},
            )
        if request.url.path == "/v5/order/realtime":
            observed_query = request.url.query.decode("utf-8")
            return httpx.Response(
                status_code=200,
                json={"retCode": 0, "retMsg": "OK", "result": {"list": []}},
            )
        return httpx.Response(status_code=404, json={"retCode": 1, "retMsg": "not found", "result": {}})

    client = BybitRestClient(
        config=settings,
        api_key="key",
        api_secret="secret",
        metrics=AppMetrics(),
    )
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        base_url=client.base_url,
        transport=httpx.MockTransport(handler),
    )

    try:
        orders = await client.fetch_open_orders()
    finally:
        await client.close()

    query = parse_qs(observed_query)
    assert orders == []
    assert query == {"category": ["linear"], "settleCoin": ["USDT"]}
