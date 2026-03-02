from __future__ import annotations

from typing import Any

from redis.asyncio import Redis

from trading_bot.marketdata.events import KlineEvent, MarketEvent, PrivateStateEvent, WalletEvent
from trading_bot.storage.redis import (
    publish_market_latest,
    publish_market_latest_interval,
    publish_private_state,
)


async def publish_market_event_cache(client: Redis, event: MarketEvent) -> None:
    payload = event.model_dump(mode="json")
    if isinstance(event, KlineEvent):
        await publish_market_latest_interval(
            client,
            event_type=event.event_type,
            interval=event.interval,
            symbol=event.symbol,
            payload=payload,
        )
        return
    await publish_market_latest(client, event_type=event.event_type, symbol=event.symbol, payload=payload)


async def publish_private_event_cache(client: Redis, event: PrivateStateEvent) -> None:
    name = "account" if isinstance(event, WalletEvent) else f"{event.event_type}s"
    await publish_private_state(client, name=name, payload=event.model_dump(mode="json"))


async def publish_private_snapshot(client: Redis, name: str, payload: dict[str, Any]) -> None:
    await publish_private_state(client, name=name, payload=payload)
