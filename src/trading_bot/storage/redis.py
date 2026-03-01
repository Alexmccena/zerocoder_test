from __future__ import annotations

import time
from datetime import datetime, timezone

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
