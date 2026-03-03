from __future__ import annotations

from trading_bot.domain.enums import ExecutionVenueKind, RunMode
from trading_bot.domain.models import AccountState, MarketSnapshot, OrderState, PositionState, RuntimeState


class RuntimeStateStore:
    def __init__(self, *, run_mode: RunMode, execution_venue: ExecutionVenueKind) -> None:
        self.state = RuntimeState(run_session_id="", run_mode=run_mode, execution_venue=execution_venue)

    def attach_run_session(self, run_session_id: str) -> None:
        self.state.run_session_id = run_session_id

    def set_account(self, account: AccountState) -> None:
        self.state.account_state = account

    def update_order(self, order: OrderState) -> None:
        if order.status in {"filled", "rejected", "expired", "cancelled"}:
            self.state.open_orders.pop(order.order_id, None)
        else:
            self.state.open_orders[order.order_id] = order

    def update_position(self, position: PositionState) -> None:
        if position.status == "open" and position.quantity > 0:
            self.state.open_positions[position.symbol] = position
        else:
            self.state.open_positions.pop(position.symbol, None)

    def update_snapshot(self, snapshot: MarketSnapshot) -> None:
        self.state.market_state_by_symbol[snapshot.symbol] = snapshot
