from __future__ import annotations

from datetime import datetime, timedelta

from trading_bot.domain.models import (
    AccountState,
    ExecutionResult,
    OrderState,
    PositionState,
    VenueConnectivityState,
    VenueStateSnapshot,
)

_OPEN_ORDER_STATUSES = {"new", "working", "partially_filled"}
_CLOSED_ORDER_STATUSES = {"filled", "rejected", "expired", "cancelled"}


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


class LiveVenueStateCache:
    def __init__(self, *, stale_after_seconds: int) -> None:
        self._stale_after = timedelta(seconds=stale_after_seconds)
        self._account_state: AccountState | None = None
        self._open_orders: dict[str, OrderState] = {}
        self._open_positions: dict[str, PositionState] = {}
        self._pending_results: list[ExecutionResult] = []
        self._connectivity_state = VenueConnectivityState()
        self._ttl_cancelled_order_ids: set[str] = set()

    def set_account(self, account: AccountState) -> None:
        self._account_state = account.model_copy(deep=True)

    def account_state(self) -> AccountState | None:
        return self._account_state.model_copy(deep=True) if self._account_state is not None else None

    def upsert_order(self, order: OrderState) -> None:
        merged = order.model_copy(deep=True)
        existing = self._open_orders.get(order.order_id)
        if existing is not None:
            merged.intent_id = merged.intent_id or existing.intent_id
            merged.client_order_id = merged.client_order_id or existing.client_order_id
            merged.exchange_order_id = merged.exchange_order_id or existing.exchange_order_id
            merged.raw_payload = {**existing.raw_payload, **merged.raw_payload}
            if merged.price is None:
                merged.price = existing.price
            if merged.stop_price is None:
                merged.stop_price = existing.stop_price
            if merged.expires_at is None:
                merged.expires_at = existing.expires_at
            if merged.cancel_reason is None:
                merged.cancel_reason = existing.cancel_reason
        if merged.order_id in self._ttl_cancelled_order_ids and merged.status == "cancelled":
            merged.status = "expired"
            merged.cancel_reason = "ttl_expired"
        if merged.status in _CLOSED_ORDER_STATUSES:
            self._open_orders.pop(merged.order_id, None)
            self._ttl_cancelled_order_ids.discard(merged.order_id)
            return
        self._open_orders[merged.order_id] = merged

    def upsert_position(self, position: PositionState) -> None:
        if position.status == "open" and position.quantity > 0:
            self._open_positions[position.symbol] = position.model_copy(deep=True)
            return
        self._open_positions.pop(position.symbol, None)

    def queue_result(self, result: ExecutionResult) -> None:
        if (
            not result.orders
            and not result.fills
            and result.position is None
            and not result.positions
            and result.account_state is None
            and result.reason is None
            and not result.payload
        ):
            return
        self._pending_results.append(result.model_copy(deep=True))

    def drain_pending(self, *, as_of: datetime) -> ExecutionResult:
        self.refresh_stale_state(as_of=as_of)
        aggregate = ExecutionResult(accepted=True)
        for result in self._pending_results:
            _merge_results(aggregate, result)
        self._pending_results.clear()
        return aggregate

    def snapshot(self, *, as_of: datetime) -> VenueStateSnapshot:
        self.refresh_stale_state(as_of=as_of)
        return VenueStateSnapshot(
            account_state=self.account_state(),
            open_orders=[order.model_copy(deep=True) for order in self._open_orders.values()],
            open_positions=[position.model_copy(deep=True) for position in self._open_positions.values()],
            connectivity_state=self._connectivity_state.model_copy(deep=True),
            as_of=as_of,
        )

    def mark_private_ws_connected(self, connected: bool) -> None:
        self._connectivity_state.private_ws_connected = connected
        if not connected:
            self._connectivity_state.stale_reason = "private_ws_disconnected"

    def note_private_event(self, *, event_ts: datetime) -> None:
        self._connectivity_state.private_ws_connected = True
        self._connectivity_state.last_private_event_at = event_ts
        self._connectivity_state.stale_reason = None

    def note_rest_sync(self, *, event_ts: datetime) -> None:
        self._connectivity_state.last_successful_rest_sync_at = event_ts
        if self._connectivity_state.stale_reason == "private_state_stale":
            self._connectivity_state.stale_reason = None

    def mark_ttl_cancel_requested(self, order_id: str) -> None:
        self._ttl_cancelled_order_ids.add(order_id)

    def is_ttl_cancel_requested(self, order_id: str) -> bool:
        return order_id in self._ttl_cancelled_order_ids

    def refresh_stale_state(self, *, as_of: datetime) -> None:
        latest_ts = self._connectivity_latest_ts()
        if latest_ts is None:
            self._connectivity_state.stale_reason = "private_state_never_synced"
            return
        if (as_of - latest_ts) > self._stale_after:
            self._connectivity_state.stale_reason = "private_state_stale"
            return
        if self._connectivity_state.stale_reason == "private_state_stale":
            self._connectivity_state.stale_reason = None

    def _connectivity_latest_ts(self) -> datetime | None:
        candidates = [
            ts
            for ts in (
                self._connectivity_state.last_private_event_at,
                self._connectivity_state.last_successful_rest_sync_at,
            )
            if ts is not None
        ]
        if not candidates:
            return None
        return max(candidates)

    @property
    def open_orders(self) -> dict[str, OrderState]:
        return {order_id: order.model_copy(deep=True) for order_id, order in self._open_orders.items()}

    @property
    def open_positions(self) -> dict[str, PositionState]:
        return {symbol: position.model_copy(deep=True) for symbol, position in self._open_positions.items()}

    def has_open_order(self, order_id: str) -> bool:
        order = self._open_orders.get(order_id)
        return order is not None and order.status in _OPEN_ORDER_STATUSES
