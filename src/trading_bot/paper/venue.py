from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from trading_bot.config.schema import AppSettings
from trading_bot.domain.enums import ExecutionVenueKind
from trading_bot.domain.models import AccountState, ExecutionPlan, ExecutionResult, MarketSnapshot, OrderState, PositionState
from trading_bot.observability.metrics import AppMetrics
from trading_bot.paper.fill_model import FillAttempt, PaperFillModel
from trading_bot.paper.ledger import PaperLedger


class PaperVenue:
    def __init__(self, *, config: AppSettings, metrics: AppMetrics) -> None:
        self.config = config
        self.metrics = metrics
        self.fill_model = PaperFillModel(execution=config.execution, paper=config.paper)
        self.ledger = PaperLedger(initial_equity=config.paper.initial_equity_usdt)
        self._orders: dict[str, OrderState] = {}

    async def submit(self, plan: ExecutionPlan) -> ExecutionResult:
        entry_order = plan.entry_order
        expires_at = None
        if entry_order.ttl_ms is not None:
            expires_at = entry_order.submitted_at + timedelta(milliseconds=entry_order.ttl_ms)
        order = OrderState(
            order_id=str(uuid4()),
            exchange_name=entry_order.exchange_name,
            execution_venue=ExecutionVenueKind.PAPER,
            symbol=entry_order.symbol,
            side=entry_order.side,
            order_type=entry_order.order_type,
            status="working" if entry_order.order_type == "limit" else "new",
            quantity=entry_order.quantity,
            price=entry_order.price,
            exchange_order_id=entry_order.client_order_id,
            client_order_id=entry_order.client_order_id,
            intent_id=entry_order.intent_id,
            time_in_force="GTC" if entry_order.order_type == "limit" else "IOC",
            submitted_at=entry_order.submitted_at,
            expires_at=expires_at,
            raw_payload={"metadata": entry_order.metadata, "plan_metadata": plan.metadata},
            created_at=entry_order.submitted_at,
            updated_at=entry_order.submitted_at,
        )
        self._orders[order.order_id] = order
        self.metrics.record_execution_plan(plan.execution_venue.value)
        self.metrics.record_paper_order(order.status, order.order_type)
        return ExecutionResult(accepted=True, orders=[order])

    async def process_market_event(self, symbol: str, snapshot: MarketSnapshot, as_of: datetime) -> ExecutionResult:
        changed_orders: list[OrderState] = []
        fills = []
        changed_position: PositionState | None = None

        for order in list(self._orders.values()):
            if order.symbol != symbol or order.status not in {"new", "working", "partially_filled"}:
                continue
            eligible_at = order.submitted_at + timedelta(milliseconds=self.config.paper.fill_latency_ms)
            if as_of < eligible_at:
                continue

            attempt = (
                self.fill_model.simulate_market_fill(order=order, snapshot=snapshot, as_of=as_of)
                if order.order_type == "market"
                else self.fill_model.simulate_limit_fill(order=order, snapshot=snapshot, as_of=as_of)
            )
            updated_order, fill, changed_position = self._apply_attempt(order, attempt, changed_position)
            if updated_order is not None:
                changed_orders.append(updated_order)
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

    async def sync_positions(self) -> list[PositionState]:
        return self.ledger.open_positions()

    def account_state(self) -> AccountState:
        return self.ledger.account_state(as_of=datetime.now(timezone.utc))

    def current_result(self, *, as_of: datetime) -> ExecutionResult:
        return ExecutionResult(
            accepted=True,
            account_state=self.ledger.account_state(as_of=as_of),
            pnl_snapshot=self.ledger.pnl_snapshot(as_of=as_of),
        )

    def _apply_attempt(
        self,
        order: OrderState,
        attempt: FillAttempt,
        changed_position: PositionState | None,
    ) -> tuple[OrderState | None, object | None, PositionState | None]:
        if attempt.reason == "expired":
            order.status = "expired"
            order.cancel_reason = "ttl_expired"
            order.updated_at = order.expires_at or order.updated_at
            self._orders.pop(order.order_id, None)
            return order.model_copy(deep=True), None, changed_position
        if attempt.fill is None and attempt.reason is not None:
            order.status = "rejected"
            order.cancel_reason = attempt.reason
            order.updated_at = order.submitted_at
            self._orders.pop(order.order_id, None)
            return order.model_copy(deep=True), None, changed_position
        if attempt.fill is None:
            return None, None, changed_position

        fill = attempt.fill
        order.filled_quantity += fill.quantity
        total_filled = order.filled_quantity
        if order.average_price is None:
            order.average_price = fill.price
        else:
            previous_quantity = total_filled - fill.quantity
            order.average_price = ((order.average_price * previous_quantity) + (fill.price * fill.quantity)) / total_filled
        order.updated_at = fill.filled_at
        if order.filled_quantity >= order.quantity:
            order.status = "filled"
            self._orders.pop(order.order_id, None)
        else:
            order.status = "partially_filled"
        changed_position = self.ledger.apply_fill(
            fill=fill,
            closed_reason=order.raw_payload.get("metadata", {}).get("close_reason"),
        )
        return order.model_copy(deep=True), fill, changed_position
