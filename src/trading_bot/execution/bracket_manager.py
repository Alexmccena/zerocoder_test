from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from trading_bot.config.schema import AppSettings
from trading_bot.domain.models import BracketState, ExecutionPlan, ExecutionResult, OrderIntent, PositionState
from trading_bot.domain.protocols import ExecutionVenue


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


def _empty_result() -> ExecutionResult:
    return ExecutionResult(accepted=True)


class BracketManager:
    def __init__(self, *, config: AppSettings, venue: ExecutionVenue) -> None:
        self.config = config
        self.venue = venue
        self._brackets: dict[str, BracketState] = {}

    def active_brackets(self) -> dict[str, BracketState]:
        return {symbol: bracket.model_copy(deep=True) for symbol, bracket in self._brackets.items()}

    def seed_bracket(self, bracket: BracketState) -> None:
        self._brackets[bracket.symbol] = bracket.model_copy(deep=True)

    def register_plan(self, *, plan: ExecutionPlan, result: ExecutionResult) -> None:
        if not plan.protective_orders or not result.orders:
            return

        stop_order = next((order for order in plan.protective_orders if order.metadata.get("order_role") == "stop_loss"), None)
        take_profit_order = next((order for order in plan.protective_orders if order.metadata.get("order_role") == "take_profit"), None)
        if stop_order is None or take_profit_order is None or stop_order.stop_price is None or take_profit_order.price is None:
            return

        entry_order = result.orders[0]
        self._brackets[entry_order.symbol] = BracketState(
            symbol=entry_order.symbol,
            intent_id=plan.intent_id,
            side="long" if plan.entry_order.side == "buy" else "short",
            quantity=entry_order.quantity,
            stop_loss_price=stop_order.stop_price,
            take_profit_price=take_profit_order.price,
            entry_order_id=entry_order.order_id,
            status="pending_entry",
            updated_at=entry_order.updated_at,
        )

    async def cancel_for_symbol(self, symbol: str, *, as_of: datetime) -> ExecutionResult:
        bracket = self._brackets.get(symbol)
        if bracket is None:
            return _empty_result()

        aggregate = _empty_result()
        for order_id in (bracket.stop_loss_order_id, bracket.take_profit_order_id):
            if order_id is None:
                continue
            cancelled = await self.venue.cancel_order(order_id, as_of=as_of)
            _merge_results(aggregate, cancelled)
        bracket.status = "closing"
        bracket.updated_at = as_of
        return aggregate

    async def on_execution_result(self, result: ExecutionResult, *, as_of: datetime) -> ExecutionResult:
        aggregate = _empty_result()
        positions = [*result.positions]
        if result.position is not None:
            positions.append(result.position)
        positions_by_symbol = {position.symbol: position for position in positions}

        for order in result.orders:
            bracket = self._brackets.get(order.symbol)
            if bracket is None:
                continue
            if order.order_id == bracket.entry_order_id and order.status in {"filled", "partially_filled"} and bracket.status == "pending_entry":
                position = positions_by_symbol.get(order.symbol)
                if position is not None and position.status == "open":
                    armed = await self._arm_bracket(bracket=bracket, position=position, as_of=as_of)
                    _merge_results(aggregate, armed)
            if order.order_id in {bracket.stop_loss_order_id, bracket.take_profit_order_id} and order.status == "filled":
                sibling_id = (
                    bracket.take_profit_order_id if order.order_id == bracket.stop_loss_order_id else bracket.stop_loss_order_id
                )
                if sibling_id is not None:
                    sibling_cancel = await self.venue.cancel_order(sibling_id, as_of=as_of)
                    _merge_results(aggregate, sibling_cancel)
                bracket.status = "closed"
                bracket.updated_at = as_of

        for position in positions:
            bracket = self._brackets.get(position.symbol)
            if bracket is None:
                continue
            if position.status != "open" or position.quantity <= 0:
                if bracket.status != "closed":
                    cancelled = await self.cancel_for_symbol(position.symbol, as_of=as_of)
                    _merge_results(aggregate, cancelled)
                self._brackets.pop(position.symbol, None)

        return aggregate

    async def rearm_bracket(self, symbol: str, *, as_of: datetime) -> ExecutionResult:
        bracket = self._brackets.get(symbol)
        if bracket is None:
            return _empty_result()

        snapshot = await self.venue.snapshot_state()
        position = next((item for item in snapshot.open_positions if item.symbol == symbol and item.status == "open"), None)
        if position is None:
            self._brackets.pop(symbol, None)
            return _empty_result()

        open_orders = {
            order.raw_payload.get("order_role"): order
            for order in snapshot.open_orders
            if order.symbol == symbol
            and order.intent_id == bracket.intent_id
            and order.status in {"new", "working", "partially_filled"}
            and order.raw_payload.get("order_role") in {"stop_loss", "take_profit"}
        }
        if "stop_loss" in open_orders:
            bracket.stop_loss_order_id = open_orders["stop_loss"].order_id
        else:
            bracket.stop_loss_order_id = None
        if "take_profit" in open_orders:
            bracket.take_profit_order_id = open_orders["take_profit"].order_id
        else:
            bracket.take_profit_order_id = None

        missing_roles = [role for role in ("stop_loss", "take_profit") if role not in open_orders]
        if not missing_roles:
            bracket.status = "armed"
            bracket.quantity = position.quantity
            bracket.updated_at = as_of
            return _empty_result()

        return await self._submit_missing_protective_orders(
            bracket=bracket,
            position=position,
            as_of=as_of,
            missing_roles=missing_roles,
        )

    async def emergency_flatten(self, symbol: str, *, as_of: datetime, reason: str) -> ExecutionResult:
        snapshot = await self.venue.snapshot_state()
        position = next((item for item in snapshot.open_positions if item.symbol == symbol and item.status == "open"), None)
        if position is None:
            return ExecutionResult(accepted=True, payload={"protection_failure": True, "protection_failure_reason": reason})

        flatten_side = "sell" if position.side == "long" else "buy"
        bracket = self._brackets.get(symbol)
        flatten_intent = OrderIntent(
            intent_id=bracket.intent_id if bracket is not None else None,
            exchange_name=position.exchange_name,
            execution_venue=position.execution_venue,
            symbol=symbol,
            side=flatten_side,
            order_type="market",
            quantity=position.quantity,
            reduce_only=True,
            submitted_at=as_of,
            metadata={"order_role": "emergency_flatten", "reason": reason},
        )
        result = await self.venue.submit(
            ExecutionPlan(
                execution_venue=position.execution_venue,
                entry_order=flatten_intent,
                intent_id=flatten_intent.intent_id or flatten_intent.client_order_id,
                metadata={"reason": reason},
            )
        )
        result.payload.update({"protection_failure": True, "protection_failure_reason": reason})
        if bracket is not None:
            bracket.status = "failed"
            bracket.last_error = reason
            bracket.updated_at = as_of
        return result

    async def _arm_bracket(self, *, bracket: BracketState, position: PositionState, as_of: datetime) -> ExecutionResult:
        return await self._submit_missing_protective_orders(
            bracket=bracket,
            position=position,
            as_of=as_of,
            missing_roles=["stop_loss", "take_profit"],
        )

    async def _submit_missing_protective_orders(
        self,
        *,
        bracket: BracketState,
        position: PositionState,
        as_of: datetime,
        missing_roles: list[str],
    ) -> ExecutionResult:
        aggregate = _empty_result()
        close_side = "sell" if position.side == "long" else "buy"
        submitted_order_ids: list[str] = []

        for role in missing_roles:
            intent = self._build_protective_intent(
                bracket=bracket,
                role=role,
                close_side=close_side,
                quantity=position.quantity,
                execution_venue=position.execution_venue,
                as_of=as_of,
            )
            result = await self.venue.submit(
                ExecutionPlan(
                    execution_venue=position.execution_venue,
                    entry_order=intent,
                    intent_id=bracket.intent_id,
                    metadata={"parent_intent_id": bracket.intent_id, "order_role": role},
                )
            )
            _merge_results(aggregate, result)
            if not result.accepted or not result.orders:
                return await self._handle_protection_failure(
                    bracket=bracket,
                    symbol=position.symbol,
                    as_of=as_of,
                    submitted_order_ids=submitted_order_ids,
                    reason="protective_arming_failed",
                    aggregate=aggregate,
                )
            submitted_order_ids.append(result.orders[0].order_id)
            if role == "stop_loss":
                bracket.stop_loss_order_id = result.orders[0].order_id
            else:
                bracket.take_profit_order_id = result.orders[0].order_id

        bracket.status = "armed"
        bracket.quantity = position.quantity
        bracket.updated_at = as_of
        return aggregate

    async def _handle_protection_failure(
        self,
        *,
        bracket: BracketState,
        symbol: str,
        as_of: datetime,
        submitted_order_ids: list[str],
        reason: str,
        aggregate: ExecutionResult,
    ) -> ExecutionResult:
        bracket.status = "failed"
        bracket.last_error = reason
        bracket.updated_at = as_of
        for order_id in submitted_order_ids:
            cancelled = await self.venue.cancel_order(order_id, as_of=as_of)
            _merge_results(aggregate, cancelled)
        if self.config.execution.flatten_on_protection_failure:
            flattened = await self.emergency_flatten(symbol, as_of=as_of, reason=reason)
            _merge_results(aggregate, flattened)
        aggregate.payload.update({"protection_failure": True, "protection_failure_reason": reason})
        return aggregate

    def _build_protective_intent(
        self,
        *,
        bracket: BracketState,
        role: str,
        close_side: str,
        quantity: Decimal,
        execution_venue,
        as_of: datetime,
    ) -> OrderIntent:
        price = bracket.take_profit_price if role == "take_profit" else None
        stop_price = bracket.stop_loss_price if role == "stop_loss" else None
        order_type = "limit" if role == "take_profit" else "stop_market"
        return OrderIntent(
            intent_id=bracket.intent_id,
            exchange_name=self.config.exchange.primary,
            execution_venue=execution_venue,
            symbol=bracket.symbol,
            side=close_side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            stop_price=stop_price,
            reduce_only=True,
            ttl_ms=None,
            submitted_at=as_of,
            metadata={"order_role": role, "parent_intent_id": bracket.intent_id},
        )
