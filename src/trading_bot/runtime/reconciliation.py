from __future__ import annotations

from datetime import datetime

from trading_bot.domain.models import ExecutionResult
from trading_bot.execution.engine import ExecutionEngine
from trading_bot.runtime.state import RuntimeStateStore


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
        events = base.payload.setdefault("events", [])
        events.extend(other.payload.get("events", []))
        for key, value in other.payload.items():
            if key != "events":
                base.payload[key] = value
    return base


class RuntimeReconciler:
    def __init__(self, *, execution_engine: ExecutionEngine) -> None:
        self.execution_engine = execution_engine

    async def reconcile(self, *, state: RuntimeStateStore, as_of: datetime) -> ExecutionResult:
        venue_state = await self.execution_engine.snapshot_state()
        aggregate = ExecutionResult(
            accepted=True,
            account_state=venue_state.account_state,
            payload={"events": []},
        )

        venue_positions = {
            position.symbol: position
            for position in venue_state.open_positions
            if position.status == "open" and position.quantity > 0
        }
        runtime_positions = dict(state.state.open_positions)

        for symbol, venue_position in venue_positions.items():
            if symbol not in runtime_positions:
                reconciled = venue_position.model_copy(deep=True)
                reconciled.raw_payload["reconciliation_event"] = "position_reconciled_from_venue"
                aggregate.positions.append(reconciled)
                aggregate.payload["events"].append("position_reconciled_from_venue")

        for symbol, runtime_position in runtime_positions.items():
            if symbol in venue_positions:
                continue
            drift_closed = runtime_position.model_copy(deep=True)
            drift_closed.status = "closed"
            drift_closed.closed_reason = "drift_closed"
            drift_closed.closed_at = as_of
            drift_closed.updated_at = as_of
            aggregate.positions.append(drift_closed)
            aggregate.payload["events"].append("position_drift_closed")
            bracket = self.execution_engine.active_brackets().get(symbol)
            if bracket is not None:
                for order_id in (bracket.stop_loss_order_id, bracket.take_profit_order_id):
                    if order_id is None:
                        continue
                    cancelled = await self.execution_engine.cancel_order(order_id, as_of=as_of)
                    _merge_results(aggregate, cancelled)

        venue_open_orders = {
            order.order_id: order
            for order in venue_state.open_orders
            if order.status in {"new", "working", "partially_filled"}
        }
        for order_id, runtime_order in list(state.state.open_orders.items()):
            if order_id in venue_open_orders:
                continue
            reconciled_order = runtime_order.model_copy(deep=True)
            reconciled_order.status = "cancelled"
            reconciled_order.cancel_reason = "reconciled_missing_on_venue"
            reconciled_order.updated_at = as_of
            aggregate.orders.append(reconciled_order)
        for order_id, venue_order in venue_open_orders.items():
            if order_id not in state.state.open_orders:
                aggregate.orders.append(venue_order.model_copy(deep=True))

        for symbol, bracket in self.execution_engine.active_brackets().items():
            if symbol not in venue_positions:
                continue
            roles = {
                order.raw_payload.get("order_role")
                for order in venue_state.open_orders
                if order.symbol == symbol
                and order.intent_id == bracket.intent_id
                and order.status in {"new", "working", "partially_filled"}
            }
            if {"stop_loss", "take_profit"}.issubset(roles):
                continue
            rearmed = await self.execution_engine.rearm_bracket(symbol, as_of=as_of)
            _merge_results(aggregate, rearmed)
            if rearmed.payload.get("protection_failure"):
                aggregate.payload["events"].append("protection_failure")

        return aggregate
