from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from redis.asyncio import Redis


def build_redis_client(redis_dsn: str) -> Redis:
    return Redis.from_url(redis_dsn, decode_responses=True)


async def ping_redis(client: Redis) -> float:
    started_at = time.perf_counter()
    await client.ping()
    return time.perf_counter() - started_at


async def publish_runtime_state(client: Redis, status: str, config_hash: str) -> None:
    await client.set("tb:runtime:status", status)
    await client.set("tb:runtime:last_heartbeat", datetime.now(timezone.utc).isoformat())
    await client.set("tb:config:active_hash", config_hash)


async def set_json(client: Redis, key: str, payload: dict[str, Any]) -> None:
    await client.set(key, json.dumps(payload, sort_keys=True))


async def publish_exchange_capabilities(client: Redis, exchange_name: str, payload: dict[str, Any]) -> None:
    await set_json(client, f"tb:exchange:{exchange_name}:capabilities", payload)


async def publish_market_latest(client: Redis, *, event_type: str, symbol: str, payload: dict[str, Any]) -> None:
    await set_json(client, f"tb:market:latest:{event_type}:{symbol}", payload)


async def publish_market_latest_interval(
    client: Redis,
    *,
    event_type: str,
    interval: str,
    symbol: str,
    payload: dict[str, Any],
) -> None:
    await set_json(client, f"tb:market:latest:{event_type}:{interval}:{symbol}", payload)


async def publish_private_state(client: Redis, *, name: str, payload: dict[str, Any]) -> None:
    await set_json(client, f"tb:state:latest:{name}", payload)
