from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal
from typing import Any

import httpx

from trading_bot.config.schema import AppSettings
from trading_bot.domain.enums import ExchangeName, ExecutionVenueKind
from trading_bot.domain.models import (
    AccountState,
    ExecutionPlan,
    ExecutionResult,
    FillState,
    MarketSnapshot,
    OrderIntent,
    OrderState,
    PositionState,
)
from trading_bot.marketdata.events import ExecutionEvent, OrderUpdateEvent, PositionUpdateEvent, WalletEvent
from trading_bot.observability.metrics import AppMetrics

from .state import LiveVenueStateCache

_TERMINAL_ORDER_STATUSES = {"filled", "rejected", "expired", "cancelled"}


def _merge_results(base: ExecutionResult, other: ExecutionResult) -> ExecutionResult:
    base.accepted = base.accepted and other.accepted
    base.orders.extend(other.orders)
    base.fills.extend(other.fills)
    if other.position is not None:
        base.position = other.position
    base.positions.extend(other.positions)
    if other.account_state is not None:
        base.account_state = other.account_state
    if other.pnl_snapshot is not None:
        base.pnl_snapshot = other.pnl_snapshot
    if other.reason is not None:
        base.reason = other.reason
    if other.payload:
        base.payload.update(other.payload)
    return base


def _floor_to_step(value: Decimal, step: Decimal | None) -> Decimal:
    if step is None or step <= 0:
        return value
    units = (value / step).to_integral_value(rounding=ROUND_DOWN)
    return units * step


class LiveVenue:
    def __init__(
        self,
        *,
        config: AppSettings,
        metrics: AppMetrics,
        rest_client: Any,
        private_ws_client: Any,
        private_message_normalizer: Callable[[dict[str, Any]], list[object]],
    ) -> None:
        self.config = config
        self.metrics = metrics
        self.rest_client = rest_client
        self.private_ws_client = private_ws_client
        self.private_message_normalizer = private_message_normalizer
        self._state = LiveVenueStateCache(stale_after_seconds=config.live.private_state_stale_after_seconds)
        self._instruments: dict[str, object] = {}
        self._private_pump_task: asyncio.Task[None] | None = None
        self._resync_task: asyncio.Task[None] | None = None
        self._connected = False

    async def connect(self) -> None:
        if self._connected:
            return
        instruments = await self.rest_client.fetch_instruments(self.config.symbols.allowlist)
        self._instruments = {instrument.symbol: instrument for instrument in instruments}
        await self._rest_resync(reason="startup")
        self._private_pump_task = asyncio.create_task(self._run_private_pump(), name="live-private-pump")
        self._resync_task = asyncio.create_task(self._run_periodic_resync(), name="live-rest-resync")
        self._connected = True

    async def close(self) -> None:
        self._connected = False
        tasks = [task for task in (self._private_pump_task, self._resync_task) if task is not None]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._private_pump_task = None
        self._resync_task = None

    async def submit(self, plan: ExecutionPlan) -> ExecutionResult:
        if self.config.runtime.dry_run:
            return ExecutionResult(accepted=True, payload={"dry_run": True})
        if not self.config.live.execution_enabled:
            return ExecutionResult(accepted=False, reason="live_execution_disabled")

        intent = plan.entry_order
        started_at = time.perf_counter()
        try:
            order = await self._submit_intent(intent=intent, plan=plan)
        except Exception as exc:
            self.metrics.record_live_order_submit(status="failed", seconds=time.perf_counter() - started_at)
            return ExecutionResult(accepted=False, reason=exc.__class__.__name__)
        self.metrics.record_live_order_submit(status="ok", seconds=time.perf_counter() - started_at)
        return ExecutionResult(accepted=True, orders=[order])

    async def process_market_event(self, symbol: str, snapshot: MarketSnapshot, as_of: datetime) -> ExecutionResult:
        aggregate = ExecutionResult(accepted=True)
        for order in list(self._state.open_orders.values()):
            if (
                order.symbol != symbol
                or order.order_type != "limit"
                or order.reduce_only
                or order.expires_at is None
                or order.expires_at > as_of
                or order.status not in {"working", "new", "partially_filled"}
            ):
                continue
            self._state.mark_ttl_cancel_requested(order.order_id)
            cancelled = await self.cancel_order(order.order_id, as_of=as_of)
            _merge_results(aggregate, cancelled)
        self._update_live_exposure_metric()
        return aggregate

    async def drain_pending_updates(self, *, as_of: datetime) -> ExecutionResult:
        pending = self._state.drain_pending(as_of=as_of)
        connectivity = self._state.snapshot(as_of=as_of).connectivity_state
        if connectivity.last_private_event_at is not None:
            age = (as_of - connectivity.last_private_event_at).total_seconds()
            self.metrics.set_live_private_ws_last_event_age(age)
        return pending

    async def cancel_order(self, order_id: str, *, as_of: datetime) -> ExecutionResult:
        if self.config.runtime.dry_run:
            return ExecutionResult(accepted=True, payload={"dry_run": True})
        if not self.config.live.execution_enabled:
            return ExecutionResult(accepted=False, reason="live_execution_disabled")
        cached = self._resolve_order(order_id)
        if cached is None:
            self.metrics.record_live_order_cancel(status="missing")
            return ExecutionResult(accepted=False, reason="order_not_found")
        try:
            await self.rest_client.cancel_order(
                symbol=cached.symbol,
                exchange_order_id=cached.exchange_order_id,
                client_order_id=cached.client_order_id,
            )
        except Exception:
            self.metrics.record_live_order_cancel(status="failed")
            raise

        timeout_seconds = self.config.live.cancel_ack_timeout_seconds
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            candidate = self._resolve_order(order_id)
            if candidate is None:
                self.metrics.record_live_order_cancel(status="ack")
                return ExecutionResult(accepted=True)
            if candidate.status in _TERMINAL_ORDER_STATUSES:
                self.metrics.record_live_order_cancel(status="ack")
                return ExecutionResult(accepted=True, orders=[candidate.model_copy(deep=True)])
            await asyncio.sleep(0.1)

        self.metrics.record_live_order_cancel(status="fallback")
        fetched = await self.rest_client.fetch_order(
            symbol=cached.symbol,
            exchange_order_id=cached.exchange_order_id,
            client_order_id=cached.client_order_id,
        )
        if fetched is not None:
            resolved = self._merge_with_cached_order(fetched, cached=cached, as_of=as_of)
            self._state.upsert_order(resolved)
            return ExecutionResult(accepted=True, orders=[resolved])

        final_order = cached.model_copy(deep=True)
        final_order.status = "expired" if self._state.is_ttl_cancel_requested(final_order.order_id) else "cancelled"
        final_order.cancel_reason = "ttl_expired" if final_order.status == "expired" else "cancelled_by_runtime"
        final_order.updated_at = as_of
        self._state.upsert_order(final_order)
        return ExecutionResult(
            accepted=True,
            orders=[final_order],
            payload={"cancel_ack_timeout_fallback": True},
        )

    async def snapshot_state(self):
        return self._state.snapshot(as_of=datetime.now(timezone.utc))

    async def sync_positions(self) -> list[PositionState]:
        await self._rest_resync(reason="position_sync")
        snapshot = self._state.snapshot(as_of=datetime.now(timezone.utc))
        return [position for position in snapshot.open_positions if position.status == "open" and position.quantity > 0]

    def account_state(self) -> AccountState:
        account = self._state.account_state()
        if account is not None:
            return account
        return AccountState(
            exchange_name=self.config.exchange.primary,
            execution_venue=ExecutionVenueKind.LIVE,
            equity=Decimal("0"),
            available_balance=Decimal("0"),
        )

    async def _submit_intent(self, *, intent: OrderIntent, plan: ExecutionPlan) -> OrderState:
        instrument = self._instruments.get(intent.symbol)
        quantity = _floor_to_step(intent.quantity, getattr(instrument, "lot_size", None))
        if quantity <= 0:
            raise ValueError("order_quantity_is_zero_after_normalization")
        price = _floor_to_step(intent.price, getattr(instrument, "tick_size", None)) if intent.price is not None else None
        stop_price = (
            _floor_to_step(intent.stop_price, getattr(instrument, "tick_size", None))
            if intent.stop_price is not None
            else None
        )
        venue_order_type = intent.order_type
        if self.config.exchange.primary == ExchangeName.BYBIT and intent.order_type == "stop_market":
            # Bybit stop-market closes are sent as market+trigger metadata.
            venue_order_type = "market"
        trigger_direction: int | None = None
        if intent.order_type == "stop_market" and stop_price is not None:
            trigger_direction = 2 if intent.side == "sell" else 1

        try:
            response = await self.rest_client.create_order(
                symbol=intent.symbol,
                side=intent.side,
                order_type=venue_order_type,
                quantity=str(quantity),
                client_order_id=intent.client_order_id,
                price=str(price) if price is not None else None,
                trigger_price=str(stop_price) if stop_price is not None else None,
                reduce_only=intent.reduce_only,
                close_on_trigger=intent.order_type == "stop_market",
                time_in_force="GTC" if intent.order_type in {"limit", "stop_market"} else None,
                trigger_direction=trigger_direction,
            )
        except (httpx.TimeoutException, httpx.HTTPStatusError):
            self.metrics.record_live_order_submit(status="unknown")
            recovered = await self.rest_client.fetch_order(symbol=intent.symbol, client_order_id=intent.client_order_id)
            if recovered is not None:
                merged = self._merge_with_intent(
                    recovered,
                    intent=intent,
                    plan_metadata=plan.metadata,
                )
                self._state.upsert_order(merged)
                return merged
            response = await self.rest_client.create_order(
                symbol=intent.symbol,
                side=intent.side,
                order_type=venue_order_type,
                quantity=str(quantity),
                client_order_id=intent.client_order_id,
                price=str(price) if price is not None else None,
                trigger_price=str(stop_price) if stop_price is not None else None,
                reduce_only=intent.reduce_only,
                close_on_trigger=intent.order_type == "stop_market",
                time_in_force="GTC" if intent.order_type in {"limit", "stop_market"} else None,
                trigger_direction=trigger_direction,
            )
            self.metrics.record_live_order_submit(status="retry_ok")

        order = OrderState(
            order_id=str(response.get("orderId", intent.client_order_id)),
            exchange_name=intent.exchange_name,
            execution_venue=ExecutionVenueKind.LIVE,
            symbol=intent.symbol,
            side=intent.side,
            order_type=intent.order_type,
            status="working" if intent.order_type in {"limit", "stop_market"} else "new",
            quantity=quantity,
            price=price,
            stop_price=stop_price,
            reduce_only=intent.reduce_only,
            exchange_order_id=response.get("orderId"),
            client_order_id=intent.client_order_id,
            intent_id=intent.intent_id,
            time_in_force="GTC" if intent.order_type in {"limit", "stop_market"} else "IOC",
            raw_payload={
                "order_role": intent.metadata.get("order_role"),
                "metadata": dict(intent.metadata),
                "plan_metadata": dict(plan.metadata),
            },
            submitted_at=intent.submitted_at,
            expires_at=(
                intent.submitted_at + timedelta(milliseconds=intent.ttl_ms)
                if intent.ttl_ms is not None
                else None
            ),
            created_at=intent.submitted_at,
            updated_at=intent.submitted_at,
        )
        self._state.upsert_order(order)
        return order.model_copy(deep=True)

    def _resolve_order(self, order_id: str) -> OrderState | None:
        if order_id in self._state.open_orders:
            return self._state.open_orders[order_id]
        for order in self._state.open_orders.values():
            if order.exchange_order_id == order_id or order.client_order_id == order_id:
                return order
        return None

    def _merge_with_intent(self, order: OrderState, *, intent: OrderIntent, plan_metadata: dict[str, object]) -> OrderState:
        merged = order.model_copy(deep=True)
        merged.execution_venue = ExecutionVenueKind.LIVE
        merged.intent_id = merged.intent_id or intent.intent_id
        merged.client_order_id = merged.client_order_id or intent.client_order_id
        merged.reduce_only = intent.reduce_only
        merged.raw_payload = {
            **merged.raw_payload,
            "order_role": intent.metadata.get("order_role"),
            "metadata": dict(intent.metadata),
            "plan_metadata": dict(plan_metadata),
        }
        merged.expires_at = (
            intent.submitted_at + timedelta(milliseconds=intent.ttl_ms)
            if intent.ttl_ms is not None
            else merged.expires_at
        )
        return merged

    def _merge_with_cached_order(self, order: OrderState, *, cached: OrderState, as_of: datetime) -> OrderState:
        merged = order.model_copy(deep=True)
        merged.execution_venue = ExecutionVenueKind.LIVE
        merged.intent_id = merged.intent_id or cached.intent_id
        merged.client_order_id = merged.client_order_id or cached.client_order_id
        merged.exchange_order_id = merged.exchange_order_id or cached.exchange_order_id
        merged.raw_payload = {**cached.raw_payload, **merged.raw_payload}
        merged.expires_at = merged.expires_at or cached.expires_at
        merged.cancel_reason = merged.cancel_reason or cached.cancel_reason
        if self._state.is_ttl_cancel_requested(cached.order_id) and merged.status == "cancelled":
            merged.status = "expired"
            merged.cancel_reason = "ttl_expired"
        merged.updated_at = as_of if merged.updated_at is None else merged.updated_at
        return merged

    async def _run_private_pump(self) -> None:
        async def handle_connection_state(connected: bool) -> None:
            self._state.mark_private_ws_connected(connected)
            if not connected:
                self.metrics.record_live_private_ws_gap()

        async for message in self.private_ws_client.stream(on_connection_state_change=handle_connection_state):
            for event in self.private_message_normalizer(message):
                event_ts = getattr(event, "event_ts", datetime.now(timezone.utc))
                self._state.note_private_event(event_ts=event_ts)
                fragment = self._event_to_execution_result(event)
                self._apply_fragment(fragment)

    async def _run_periodic_resync(self) -> None:
        interval = self.config.live.rest_resync_interval_seconds
        while True:
            await asyncio.sleep(interval)
            await self._rest_resync(reason="periodic")

    async def _rest_resync(self, *, reason: str) -> None:
        as_of = datetime.now(timezone.utc)
        try:
            account, open_orders, open_positions = await asyncio.gather(
                self.rest_client.fetch_account_state(),
                self.rest_client.fetch_open_orders(),
                self.rest_client.fetch_positions(),
            )
        except Exception:
            self.metrics.record_live_rest_resync(status="failed")
            raise

        self.metrics.record_live_rest_resync(status="ok")
        aggregate = ExecutionResult(accepted=True, payload={"rest_resync_reason": reason})
        account.execution_venue = ExecutionVenueKind.LIVE
        self._state.set_account(account)
        aggregate.account_state = account

        previous_orders = self._state.open_orders
        current_orders: dict[str, OrderState] = {}
        for order in open_orders:
            merged = self._merge_with_cached_order(order, cached=previous_orders.get(order.order_id, order), as_of=as_of)
            current_orders[merged.order_id] = merged
            self._state.upsert_order(merged)
            aggregate.orders.append(merged)
        for order_id, previous in previous_orders.items():
            if order_id in current_orders:
                continue
            closed = previous.model_copy(deep=True)
            closed.status = "expired" if self._state.is_ttl_cancel_requested(order_id) else "cancelled"
            closed.cancel_reason = "ttl_expired" if closed.status == "expired" else "reconciled_missing_on_venue"
            closed.updated_at = as_of
            self._state.upsert_order(closed)
            aggregate.orders.append(closed)

        seen_symbols: set[str] = set()
        for position in open_positions:
            position.execution_venue = ExecutionVenueKind.LIVE
            self._state.upsert_position(position)
            seen_symbols.add(position.symbol)
            aggregate.positions.append(position)
        for symbol, existing in self._state.open_positions.items():
            if symbol in seen_symbols:
                continue
            closed_position = existing.model_copy(deep=True)
            closed_position.status = "closed"
            closed_position.closed_reason = "reconciled_missing_on_venue"
            closed_position.closed_at = as_of
            closed_position.updated_at = as_of
            self._state.upsert_position(closed_position)
            aggregate.positions.append(closed_position)

        self._state.note_rest_sync(event_ts=as_of)
        self._state.queue_result(aggregate)

    def _event_to_execution_result(self, event) -> ExecutionResult:
        if isinstance(event, WalletEvent):
            account = AccountState(
                exchange_name=event.exchange_name,
                execution_venue=ExecutionVenueKind.LIVE,
                equity=event.equity,
                available_balance=event.available_balance,
                wallet_balance=event.wallet_balance,
                margin_balance=event.margin_balance,
                unrealized_pnl=event.unrealized_pnl,
                account_type=event.account_type,
                raw_payload=event.raw_payload,
                updated_at=event.event_ts,
            )
            return ExecutionResult(accepted=True, account_state=account)
        if isinstance(event, OrderUpdateEvent):
            order = event.order.model_copy(deep=True)
            order.execution_venue = ExecutionVenueKind.LIVE
            return ExecutionResult(accepted=True, orders=[order])
        if isinstance(event, ExecutionEvent):
            return ExecutionResult(
                accepted=True,
                fills=[
                    FillState(
                        order_id=event.order_id,
                        execution_venue=ExecutionVenueKind.LIVE,
                        exchange_name=event.exchange_name,
                        symbol=event.symbol,
                        side=event.side,
                        price=event.price,
                        quantity=event.quantity,
                        fee=event.fee,
                        liquidity_type=event.liquidity_type,
                        exchange_fill_id=event.exchange_fill_id,
                        raw_payload=event.raw_payload,
                        filled_at=event.filled_at,
                    )
                ],
            )
        if isinstance(event, PositionUpdateEvent):
            position = event.position.model_copy(deep=True)
            position.execution_venue = ExecutionVenueKind.LIVE
            return ExecutionResult(accepted=True, positions=[position])
        return ExecutionResult(accepted=True)

    def _apply_fragment(self, fragment: ExecutionResult) -> None:
        if fragment.account_state is not None:
            self._state.set_account(fragment.account_state)
        for order in fragment.orders:
            self._state.upsert_order(order)
        for position in fragment.positions:
            self._state.upsert_position(position)
        if fragment.position is not None:
            self._state.upsert_position(fragment.position)
        self._state.queue_result(fragment)

    def _update_live_exposure_metric(self) -> None:
        total_exposure = Decimal("0")
        for position in self._state.open_positions.values():
            reference_price = position.mark_price or position.last_price or position.entry_price
            total_exposure += position.quantity * reference_price
        self.metrics.set_live_total_exposure_usdt(float(total_exposure))
