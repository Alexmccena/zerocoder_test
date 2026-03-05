from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from redis.asyncio import Redis
from structlog.stdlib import BoundLogger

from trading_bot.config.schema import AppSettings
from trading_bot.domain.models import AccountState, ExchangeCapabilities, FillState
from trading_bot.marketdata.cache import publish_market_event_cache, publish_private_event_cache, publish_private_snapshot
from trading_bot.marketdata.events import (
    ExecutionEvent,
    FundingRateEvent,
    MarketEvent,
    OpenInterestEvent,
    OrderUpdateEvent,
    PositionUpdateEvent,
    PrivateStateEvent,
    WalletEvent,
)
from trading_bot.observability.metrics import AppMetrics
from trading_bot.storage.parquet import ParquetArchiveWriter
from trading_bot.storage.redis import publish_exchange_capabilities
from trading_bot.storage.repositories import (
    AccountSnapshotRepository,
    ConfigSnapshotRepository,
    FillRepository,
    InstrumentRepository,
    OrderRepository,
    PositionRepository,
    RunSessionRepository,
)


@dataclass(slots=True)
class CaptureService:
    config: AppSettings
    config_hash: str
    logger: BoundLogger
    metrics: AppMetrics
    redis_client: Redis
    run_sessions: RunSessionRepository
    config_snapshots: ConfigSnapshotRepository
    instruments: InstrumentRepository
    account_snapshots: AccountSnapshotRepository
    orders: OrderRepository
    fills: FillRepository
    positions: PositionRepository
    archive_writer: ParquetArchiveWriter
    capabilities: ExchangeCapabilities
    fetch_instruments: Callable[[], Awaitable[list[Any]]]
    stream_public_events: Callable[[], AsyncIterator[MarketEvent]]
    fetch_open_interest: Callable[[str], Awaitable[OpenInterestEvent | None]]
    fetch_funding_rate: Callable[[str], Awaitable[FundingRateEvent | None]]
    fetch_account_state: Callable[[], Awaitable[AccountState]] | None = None
    fetch_open_orders: Callable[[], Awaitable[list[Any]]] | None = None
    fetch_positions: Callable[[], Awaitable[list[Any]]] | None = None
    stream_private_events: Callable[[], AsyncIterator[PrivateStateEvent]] | None = None

    async def run(self, *, duration_seconds: int | None = None) -> None:
        self.metrics.record_capture_run()
        run_session = await self.run_sessions.create(
            run_mode=self.config.runtime.mode.value,
            environment=self.config.runtime.environment.value,
            status="running",
        )
        try:
            await self.config_snapshots.create(
                run_session_id=run_session.id,
                config_hash=self.config_hash,
                config_json=self.config.model_dump(mode="json"),
            )
            await publish_exchange_capabilities(
                self.redis_client,
                self.capabilities.exchange_name.value,
                self.capabilities.model_dump(mode="json"),
            )
            await self.instruments.upsert_many(await self.fetch_instruments())
            if self.fetch_account_state is not None:
                await self._sync_private_state(run_session.id)
            await self._consume_streams(run_session.id, duration_seconds=duration_seconds)
        except Exception as exc:
            await self.run_sessions.mark_failed(run_session.id, reason=exc.__class__.__name__)
            self.logger.exception("capture_run_failed", run_session_id=run_session.id)
            raise
        else:
            await self.run_sessions.mark_completed(run_session.id)
            self.logger.info("capture_run_completed", run_session_id=run_session.id)
        finally:
            await self.archive_writer.flush()

    async def _consume_streams(self, run_session_id: str, *, duration_seconds: int | None) -> None:
        queue: asyncio.Queue[MarketEvent | PrivateStateEvent] = asyncio.Queue()
        tasks = [
            asyncio.create_task(self._pump_public_events(queue), name="public-stream"),
            asyncio.create_task(self._poll_rest_market_data(queue), name="rest-poll"),
            asyncio.create_task(self._periodic_flush(), name="archive-flush"),
        ]
        if self.stream_private_events is not None:
            tasks.append(asyncio.create_task(self._pump_private_events(queue), name="private-stream"))

        stop_task: asyncio.Task[None] | None = None
        if duration_seconds is not None:
            stop_task = asyncio.create_task(asyncio.sleep(duration_seconds))

        try:
            while True:
                for task in tasks:
                    if task.done():
                        exception = task.exception()
                        if exception is not None:
                            raise exception
                if stop_task is not None and stop_task.done() and queue.empty():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.25)
                except asyncio.TimeoutError:
                    continue
                await self._dispatch_event(run_session_id, event)
        finally:
            for task in tasks:
                task.cancel()
            if stop_task is not None:
                stop_task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError):
                    await task
            if stop_task is not None:
                with suppress(asyncio.CancelledError):
                    await stop_task

    async def _pump_public_events(self, queue: asyncio.Queue[MarketEvent | PrivateStateEvent]) -> None:
        async for event in self.stream_public_events():
            await queue.put(event)

    async def _pump_private_events(self, queue: asyncio.Queue[MarketEvent | PrivateStateEvent]) -> None:
        assert self.stream_private_events is not None
        async for event in self.stream_private_events():
            await queue.put(event)

    async def _poll_rest_market_data(self, queue: asyncio.Queue[MarketEvent | PrivateStateEvent]) -> None:
        interval = min(
            self.config.market_data.open_interest_poll_interval_seconds,
            self.config.market_data.funding_poll_interval_seconds,
        )
        while True:
            for symbol in self.config.symbols.allowlist:
                if self.config.market_data.enable_open_interest:
                    event = await self.fetch_open_interest(symbol)
                    if event is not None:
                        await queue.put(event)
                if self.config.market_data.enable_funding:
                    event = await self.fetch_funding_rate(symbol)
                    if event is not None:
                        await queue.put(event)
            await asyncio.sleep(interval)

    async def _periodic_flush(self) -> None:
        while True:
            await asyncio.sleep(self.config.storage.parquet_flush_seconds)
            await self.archive_writer.flush()

    async def _sync_private_state(self, run_session_id: str) -> None:
        assert self.fetch_account_state is not None
        assert self.fetch_open_orders is not None
        assert self.fetch_positions is not None
        try:
            account = await self.fetch_account_state()
            await self.account_snapshots.create(run_session_id=run_session_id, account=account)
            await publish_private_snapshot(self.redis_client, "account", account.model_dump(mode="json"))
            orders = await self.fetch_open_orders()
            for order in orders:
                await self.orders.upsert_from_exchange(run_session_id=run_session_id, order=order)
            await publish_private_snapshot(
                self.redis_client,
                "open_orders",
                {"items": [order.model_dump(mode="json") for order in orders]},
            )
            positions = await self.fetch_positions()
            for position in positions:
                await self.positions.upsert_snapshot(run_session_id=run_session_id, position=position)
            await publish_private_snapshot(
                self.redis_client,
                "positions",
                {"items": [position.model_dump(mode="json") for position in positions]},
            )
            self.metrics.record_private_state_sync(success=True)
        except Exception:
            self.metrics.record_private_state_sync(success=False)
            raise

    async def _dispatch_event(self, run_session_id: str, event: MarketEvent | PrivateStateEvent) -> None:
        if isinstance(event, MarketEvent):
            lag_seconds = (datetime.now(timezone.utc) - event.event_ts).total_seconds()
            self.metrics.record_market_event(event.event_type, lag_seconds=lag_seconds)
            await self.archive_writer.append(event)
            try:
                await publish_market_event_cache(self.redis_client, event)
            except Exception:
                self.metrics.record_redis_publish_failure()
                self.logger.warning("redis_market_cache_publish_failed", event_type=event.event_type, symbol=event.symbol)
            return
        if isinstance(event, WalletEvent):
            account = AccountState(
                exchange_name=event.exchange_name,
                equity=event.equity,
                available_balance=event.available_balance,
                wallet_balance=event.wallet_balance,
                margin_balance=event.margin_balance,
                unrealized_pnl=event.unrealized_pnl,
                account_type=event.account_type,
                raw_payload=event.raw_payload,
            )
            await self.account_snapshots.create(run_session_id=run_session_id, account=account)
        elif isinstance(event, OrderUpdateEvent):
            await self.orders.upsert_from_exchange(run_session_id=run_session_id, order=event.order)
        elif isinstance(event, ExecutionEvent):
            fill = FillState(
                order_id=event.order_id,
                exchange_name=event.exchange_name,
                symbol=event.symbol,
                side=event.side,
                price=event.price,
                quantity=event.quantity,
                fee=event.fee,
                liquidity_type=event.liquidity_type,
                is_maker=event.liquidity_type == "maker",
                exchange_fill_id=event.exchange_fill_id,
                raw_payload=event.raw_payload,
                filled_at=event.filled_at,
            )
            await self.fills.insert_if_new(fill)
        elif isinstance(event, PositionUpdateEvent):
            await self.positions.upsert_snapshot(run_session_id=run_session_id, position=event.position)

        try:
            await publish_private_event_cache(self.redis_client, event)
        except Exception:
            self.metrics.record_redis_publish_failure()
            self.logger.warning("redis_private_cache_publish_failed", event_type=event.event_type)
