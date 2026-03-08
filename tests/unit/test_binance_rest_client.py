from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest

from trading_bot.adapters.exchanges.binance.rest import BinanceRestClient
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
                "primary": "binance",
                "market_type": "linear_perp",
                "position_mode": "one_way",
                "account_alias": "default",
                "testnet": testnet,
                "private_state_enabled": True,
                "recv_window_ms": 5000,
            },
            "symbols": {"allowlist": ["ETHUSDT"]},
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


def _time_response() -> httpx.Response:
    return httpx.Response(status_code=200, json={"serverTime": 1_700_000_000_000})


async def test_fetch_open_orders_requires_list_payload() -> None:
    settings = _build_settings(testnet=False)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/fapi/v1/time":
            return _time_response()
        if request.url.path == "/fapi/v1/openOrders":
            return httpx.Response(status_code=200, json={})
        return httpx.Response(status_code=404, json={"msg": "not found"})

    client = BinanceRestClient(
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
        with pytest.raises(RuntimeError, match="expected list payload"):
            await client.fetch_open_orders()
    finally:
        await client.close()


async def test_fetch_positions_requires_list_payload() -> None:
    settings = _build_settings(testnet=False)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/fapi/v1/time":
            return _time_response()
        if request.url.path == "/fapi/v2/positionRisk":
            return httpx.Response(status_code=200, json={})
        return httpx.Response(status_code=404, json={"msg": "not found"})

    client = BinanceRestClient(
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
        with pytest.raises(RuntimeError, match="expected list payload"):
            await client.fetch_positions()
    finally:
        await client.close()


async def test_create_stop_market_close_position_omits_reduce_only_and_quantity() -> None:
    settings = _build_settings(testnet=False)
    observed_query = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed_query
        if request.url.path == "/fapi/v1/time":
            return _time_response()
        if request.url.path == "/fapi/v1/order":
            observed_query = request.url.query.decode("utf-8")
            return httpx.Response(status_code=200, json={"orderId": 123})
        return httpx.Response(status_code=404, json={"msg": "not found"})

    client = BinanceRestClient(
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
        await client.create_order(
            symbol="ETHUSDT",
            side="sell",
            order_type="stop_market",
            quantity="0.01",
            client_order_id="cid-1",
            trigger_price="1983.6",
            reduce_only=True,
            close_on_trigger=True,
            time_in_force="GTC",
        )
    finally:
        await client.close()

    query = parse_qs(observed_query)
    assert query["type"] == ["STOP_MARKET"]
    assert query["closePosition"] == ["true"]
    assert query["stopPrice"] == ["1983.6"]
    assert "quantity" not in query
    assert "reduceOnly" not in query


async def test_create_stop_market_reduce_only_with_quantity() -> None:
    settings = _build_settings(testnet=False)
    observed_query = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed_query
        if request.url.path == "/fapi/v1/time":
            return _time_response()
        if request.url.path == "/fapi/v1/order":
            observed_query = request.url.query.decode("utf-8")
            return httpx.Response(status_code=200, json={"orderId": 456})
        return httpx.Response(status_code=404, json={"msg": "not found"})

    client = BinanceRestClient(
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
        await client.create_order(
            symbol="ETHUSDT",
            side="sell",
            order_type="stop_market",
            quantity="0.02",
            client_order_id="cid-2",
            trigger_price="1980.0",
            reduce_only=True,
            close_on_trigger=False,
            time_in_force="GTC",
        )
    finally:
        await client.close()

    query = parse_qs(observed_query)
    assert query["type"] == ["STOP_MARKET"]
    assert query["quantity"] == ["0.02"]
    assert query["reduceOnly"] == ["true"]
    assert "closePosition" not in query


async def test_create_order_rejects_close_on_trigger_for_non_stop_market() -> None:
    settings = _build_settings(testnet=False)
    client = BinanceRestClient(
        config=settings,
        api_key="key",
        api_secret="secret",
        metrics=AppMetrics(),
    )
    try:
        with pytest.raises(ValueError, match="close_on_trigger"):
            await client.create_order(
                symbol="ETHUSDT",
                side="buy",
                order_type="market",
                quantity="0.01",
                client_order_id="cid-3",
                close_on_trigger=True,
            )
    finally:
        await client.close()
