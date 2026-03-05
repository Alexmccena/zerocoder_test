from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from trading_bot.domain.models import BracketState, ExecutionResult, OrderState, PositionState

_OPEN_ORDER_STATUSES = {"new", "working", "partially_filled"}


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


@dataclass(frozen=True, slots=True)
class LiveRecoveryResult:
    success: bool
    halt_reason: str | None
    execution_result: ExecutionResult


def _enrich_order_from_db(order: OrderState, db_record) -> OrderState:
    enriched = order.model_copy(deep=True)
    enriched.intent_id = enriched.intent_id or db_record.intent_id
    enriched.client_order_id = enriched.client_order_id or db_record.client_order_id
    enriched.exchange_order_id = enriched.exchange_order_id or db_record.exchange_order_id
    payload_json = dict(getattr(db_record, "payload_json", {}) or {})
    enriched.raw_payload = {**payload_json, **enriched.raw_payload}
    if enriched.price is None:
        enriched.price = db_record.price
    if enriched.stop_price is None:
        enriched.stop_price = db_record.stop_price
    if enriched.expires_at is None:
        enriched.expires_at = db_record.expires_at
    return enriched


def _build_lookup(records: list) -> tuple[dict[str, object], dict[str, object]]:
    by_exchange: dict[str, object] = {}
    by_client: dict[str, object] = {}
    for record in records:
        exchange_order_id = getattr(record, "exchange_order_id", None)
        client_order_id = getattr(record, "client_order_id", None)
        if exchange_order_id:
            by_exchange[str(exchange_order_id)] = record
        if client_order_id:
            by_client[str(client_order_id)] = record
    return by_exchange, by_client


async def recover_live_runtime_state(
    *,
    execution_engine,
    state_store,
    order_repository,
    as_of: datetime,
    startup_recovery_policy: str,
) -> LiveRecoveryResult:
    venue_snapshot = await execution_engine.snapshot_state()
    persisted_open_orders = await order_repository.list_open_orders(execution_venue="live")
    by_exchange_id, by_client_order_id = _build_lookup(persisted_open_orders)

    aggregate = ExecutionResult(accepted=True)
    recovered_orders: list[OrderState] = []
    open_positions_by_symbol: dict[str, PositionState] = {}

    for order in venue_snapshot.open_orders:
        db_record = None
        if order.exchange_order_id:
            db_record = by_exchange_id.get(order.exchange_order_id)
        if db_record is None and order.client_order_id:
            db_record = by_client_order_id.get(order.client_order_id)
        enriched = _enrich_order_from_db(order, db_record) if db_record is not None else order.model_copy(deep=True)
        state_store.update_order(enriched)
        recovered_orders.append(enriched)

    for position in venue_snapshot.open_positions:
        state_store.update_position(position)
        if position.status == "open" and position.quantity > 0:
            open_positions_by_symbol[position.symbol] = position.model_copy(deep=True)

    orders_by_symbol_intent: dict[tuple[str, str], list[OrderState]] = {}
    for order in recovered_orders:
        if order.status not in _OPEN_ORDER_STATUSES:
            continue
        if not order.intent_id:
            continue
        orders_by_symbol_intent.setdefault((order.symbol, order.intent_id), []).append(order)

    for symbol, position in open_positions_by_symbol.items():
        intents_for_symbol = [item for item in orders_by_symbol_intent.items() if item[0][0] == symbol]
        if not intents_for_symbol:
            if startup_recovery_policy == "flatten":
                flattened = await execution_engine.emergency_flatten(
                    symbol,
                    as_of=as_of,
                    reason="startup_recovery_missing_bracket",
                )
                _merge_results(aggregate, flattened)
                continue
            return LiveRecoveryResult(
                success=False,
                halt_reason=f"startup_unprotected_position:{symbol}",
                execution_result=aggregate,
            )

        selected_key, orders = max(intents_for_symbol, key=lambda item: len(item[1]))
        intent_id = selected_key[1]
        by_role = {order.raw_payload.get("order_role"): order for order in orders}
        entry = by_role.get("entry")
        stop_loss = by_role.get("stop_loss")
        take_profit = by_role.get("take_profit")
        stop_price = stop_loss.stop_price if stop_loss is not None else None
        take_profit_price = take_profit.price if take_profit is not None else None

        if stop_price is None or take_profit_price is None:
            if startup_recovery_policy == "flatten":
                flattened = await execution_engine.emergency_flatten(
                    symbol,
                    as_of=as_of,
                    reason="startup_recovery_incomplete_protection",
                )
                _merge_results(aggregate, flattened)
                continue
            return LiveRecoveryResult(
                success=False,
                halt_reason=f"startup_incomplete_protection:{symbol}",
                execution_result=aggregate,
            )

        bracket = BracketState(
            symbol=symbol,
            intent_id=intent_id,
            side=position.side,
            quantity=position.quantity,
            stop_loss_price=stop_price,
            take_profit_price=take_profit_price,
            entry_order_id=entry.order_id if entry is not None else None,
            stop_loss_order_id=stop_loss.order_id if stop_loss is not None else None,
            take_profit_order_id=take_profit.order_id if take_profit is not None else None,
            status="armed" if stop_loss is not None and take_profit is not None else "pending_entry",
            updated_at=as_of,
        )
        execution_engine.seed_bracket(bracket)
        if stop_loss is None or take_profit is None:
            rearmed = await execution_engine.rearm_bracket(symbol, as_of=as_of)
            _merge_results(aggregate, rearmed)
            if rearmed.payload.get("protection_failure"):
                if startup_recovery_policy == "flatten":
                    flattened = await execution_engine.emergency_flatten(
                        symbol,
                        as_of=as_of,
                        reason="startup_recovery_rearm_failed",
                    )
                    _merge_results(aggregate, flattened)
                    continue
                return LiveRecoveryResult(
                    success=False,
                    halt_reason=f"startup_rearm_failed:{symbol}",
                    execution_result=aggregate,
                )

    for order in recovered_orders:
        role = order.raw_payload.get("order_role")
        if role not in {"stop_loss", "take_profit"}:
            continue
        if order.symbol in open_positions_by_symbol:
            continue
        cancelled = await execution_engine.cancel_order(order.order_id, as_of=as_of)
        _merge_results(aggregate, cancelled)

    state_store.sync_brackets(execution_engine.active_brackets())
    return LiveRecoveryResult(success=True, halt_reason=None, execution_result=aggregate)
