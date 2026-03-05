from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from trading_bot.config.schema import AppSettings
from trading_bot.domain.enums import ExecutionVenueKind
from trading_bot.domain.models import (
    AccountState,
    ExecutionPlan,
    ExecutionResult,
    MarketSnapshot,
    OrderState,
    PositionState,
    VenueStateSnapshot,
)
from trading_bot.observability.metrics import AppMetrics
from trading_bot.paper.fill_model import FillAttempt, PaperFillModel
from trading_bot.paper.ledger import PaperLedger


def _active_status(order: OrderState) -> bool:
    return order.status in {"new", "working", "partially_filled"}


class PaperVenue:
    def __init__(self, *, config: AppSettings, metrics: AppMetrics) -> None:
        self.config = config
        self.metrics = metrics
        self.fill_model = PaperFillModel(execution=config.execution, paper=config.paper)
        self.ledger = PaperLedger(initial_equity=config.paper.initial_equity_usdt)
        self._orders: dict[str, OrderState] = {}

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def submit(self, plan: ExecutionPlan) -> ExecutionResult:
        entry_order = plan.entry_order
        expires_at = None
        if entry_order.ttl_ms is not None:
            expires_at = entry_order.submitted_at + timedelta(milliseconds=entry_order.ttl_ms)
        order_role = entry_order.metadata.get("order_role")
        order_type = entry_order.order_type
        order = OrderState(
            order_id=str(uuid4()),
            exchange_name=entry_order.exchange_name,
            execution_venue=ExecutionVenueKind.PAPER,
            symbol=entry_order.symbol,
            side=entry_order.side,
            order_type=order_type,
            status="working" if order_type in {"limit", "stop_market"} else "new",
            quantity=entry_order.quantity,
            price=entry_order.price,
            stop_price=entry_order.stop_price,
            reduce_only=entry_order.reduce_only,
            exchange_order_id=entry_order.client_order_id,
            client_order_id=entry_order.client_order_id,
            intent_id=entry_order.intent_id,
            time_in_force="GTC" if order_type in {"limit", "stop_market"} else "IOC",
            submitted_at=entry_order.submitted_at,
            expires_at=expires_at,
            raw_payload={
                "order_role": order_role,
                "metadata": dict(entry_order.metadata),
                "plan_metadata": dict(plan.metadata),
            },
            created_at=entry_order.submitted_at,
            updated_at=entry_order.submitted_at,
        )
        self._orders[order.order_id] = order
        self.metrics.record_execution_plan(plan.execution_venue.value)
        self.metrics.record_paper_order(order.status, order.order_type)
        return ExecutionResult(accepted=True, orders=[order.model_copy(deep=True)])

    async def process_market_event(self, symbol: str, snapshot: MarketSnapshot, as_of: datetime) -> ExecutionResult:
        changed_orders: list[OrderState] = []
        fills = []
        changed_position: PositionState | None = None

        for order in list(self._orders.values()):
            if order.symbol != symbol or not _active_status(order):
                continue
            eligible_at = order.submitted_at + timedelta(milliseconds=self.config.paper.fill_latency_ms)
            if as_of < eligible_at:
                continue

            if order.order_type == "market":
                attempt = self.fill_model.simulate_market_fill(order=order, snapshot=snapshot, as_of=as_of)
            elif order.order_type == "limit":
                attempt = self.fill_model.simulate_limit_fill(order=order, snapshot=snapshot, as_of=as_of)
            else:
                attempt = self.fill_model.simulate_stop_market_fill(order=order, snapshot=snapshot, as_of=as_of)

            order_updates, fill, changed_position = self._apply_attempt(order, attempt, changed_position)
            changed_orders.extend(order_updates)
            if fill is not None:
                fills.append(fill)
                latency_seconds = max((fill.filled_at - order.submitted_at).total_seconds(), 0.0)
                self.metrics.record_paper_fill(fill.liquidity_type, latency_seconds)

        self.ledger.mark_to_market(symbol=symbol, snapshot=snapshot)
        account_state = None
        pnl_snapshot = None
        if changed_orders or fills or symbol in self.ledger.positions:
            account_state = self.ledger.account_state(as_of=as_of)
            pnl_snapshot = self.ledger.pnl_snapshot(as_of=as_of)
            self.metrics.set_paper_realized_pnl(float(pnl_snapshot.realized_pnl))
            self.metrics.set_paper_unrealized_pnl(float(pnl_snapshot.unrealized_pnl))

        return ExecutionResult(
            accepted=True,
            orders=changed_orders,
            fills=fills,
            position=changed_position,
            account_state=account_state,
            pnl_snapshot=pnl_snapshot,
        )

    async def drain_pending_updates(self, *, as_of: datetime) -> ExecutionResult:
        return ExecutionResult(accepted=True)

    async def cancel_order(self, order_id: str, *, as_of: datetime) -> ExecutionResult:
        order = self._orders.get(order_id)
        if order is None:
            return ExecutionResult(accepted=False, reason="order_not_found")
        if not _active_status(order):
            return ExecutionResult(accepted=False, reason="order_not_cancellable")

        order.status = "cancelled"
        order.cancel_reason = "cancelled_by_runtime"
        order.updated_at = as_of
        self._orders.pop(order_id, None)
        return ExecutionResult(accepted=True, orders=[order.model_copy(deep=True)])

    async def snapshot_state(self) -> VenueStateSnapshot:
        as_of = datetime.now(timezone.utc)
        return VenueStateSnapshot(
            account_state=self.ledger.account_state(as_of=as_of),
            open_orders=[order.model_copy(deep=True) for order in self._orders.values() if _active_status(order)],
            open_positions=self.ledger.open_positions(),
            as_of=as_of,
        )

    async def sync_positions(self) -> list[PositionState]:
        return self.ledger.open_positions()

    def account_state(self) -> AccountState:
        return self.ledger.account_state(as_of=datetime.now(timezone.utc))

    def _apply_attempt(
        self,
        order: OrderState,
        attempt: FillAttempt,
        changed_position: PositionState | None,
    ) -> tuple[list[OrderState], object | None, PositionState | None]:
        if attempt.reason == "expired":
            order.status = "expired"
            order.cancel_reason = "ttl_expired"
            order.updated_at = order.expires_at or order.updated_at
            self._orders.pop(order.order_id, None)
            return [order.model_copy(deep=True)], None, changed_position
        if attempt.fill is None and attempt.reason is not None:
            order.status = "rejected"
            order.cancel_reason = attempt.reason
            order.updated_at = order.submitted_at
            self._orders.pop(order.order_id, None)
            return [order.model_copy(deep=True)], None, changed_position
        if attempt.fill is None:
            return [], None, changed_position

        fill, reduce_only_reason = self._enforce_reduce_only(order=order, fill=attempt.fill)
        if fill is None:
            order.status = "cancelled"
            order.cancel_reason = reduce_only_reason
            order.updated_at = attempt.fill.filled_at
            self._orders.pop(order.order_id, None)
            return [order.model_copy(deep=True)], None, changed_position

        order.filled_quantity += fill.quantity
        total_filled = order.filled_quantity
        if order.average_price is None:
            order.average_price = fill.price
        else:
            previous_quantity = total_filled - fill.quantity
            order.average_price = ((order.average_price * previous_quantity) + (fill.price * fill.quantity)) / total_filled
        order.updated_at = fill.filled_at

        changed_position = self.ledger.apply_fill(
            fill=fill,
            closed_reason=order.raw_payload.get("metadata", {}).get("close_reason"),
        )

        if order.filled_quantity >= order.quantity or (order.reduce_only and self.ledger.positions.get(order.symbol) is None):
            order.status = "filled"
            self._orders.pop(order.order_id, None)
        else:
            order.status = "partially_filled"

        changed_orders = [order.model_copy(deep=True)]
        changed_orders.extend(self._cancel_protective_sibling(order=order, as_of=fill.filled_at))
        return changed_orders, fill, changed_position

    def _enforce_reduce_only(self, *, order: OrderState, fill) -> tuple[object | None, str | None]:
        if not order.reduce_only:
            return fill, None

        position = self.ledger.positions.get(order.symbol)
        if position is None:
            return None, "reduce_only_no_position"
        expected_side = "sell" if position.side == "long" else "buy"
        if order.side != expected_side:
            return None, "reduce_only_wrong_side"

        allowed_quantity = min(fill.quantity, position.quantity)
        if allowed_quantity <= 0:
            return None, "reduce_only_no_quantity"
        if allowed_quantity == fill.quantity:
            return fill, None
        ratio = allowed_quantity / fill.quantity
        return (
            fill.model_copy(
                update={
                    "quantity": allowed_quantity,
                    "fee": fill.fee * ratio,
                }
            ),
            None,
        )

    def _cancel_protective_sibling(self, *, order: OrderState, as_of: datetime) -> list[OrderState]:
        order_role = order.raw_payload.get("order_role")
        if order.status != "filled" or order_role not in {"stop_loss", "take_profit"}:
            return []

        changed_orders: list[OrderState] = []
        for sibling in list(self._orders.values()):
            sibling_role = sibling.raw_payload.get("order_role")
            if (
                sibling.order_id == order.order_id
                or sibling.symbol != order.symbol
                or sibling.intent_id != order.intent_id
                or sibling_role not in {"stop_loss", "take_profit"}
                or not _active_status(sibling)
            ):
                continue
            sibling.status = "cancelled"
            sibling.cancel_reason = "sibling_filled"
            sibling.updated_at = as_of
            self._orders.pop(sibling.order_id, None)
            changed_orders.append(sibling.model_copy(deep=True))
        return changed_orders
