from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from trading_bot.domain.enums import ExchangeName, ExecutionVenueKind, PositionMode
from trading_bot.domain.models import AccountState, FillState, MarketSnapshot, PnlSnapshot, PositionState


def _unrealized(side: str, entry_price: Decimal, mark_price: Decimal, quantity: Decimal) -> Decimal:
    if side == "long":
        return (mark_price - entry_price) * quantity
    return (entry_price - mark_price) * quantity


@dataclass(slots=True)
class PaperLedger:
    initial_equity: Decimal
    balance: Decimal = field(init=False)
    realized_pnl: Decimal = field(default=Decimal("0"))
    fees_paid: Decimal = field(default=Decimal("0"))
    positions: dict[str, PositionState] = field(default_factory=dict)
    peak_equity: Decimal = field(init=False)

    def __post_init__(self) -> None:
        self.balance = self.initial_equity
        self.peak_equity = self.initial_equity

    def account_state(self, *, as_of: datetime) -> AccountState:
        unrealized = sum((position.unrealized_pnl for position in self.positions.values()), start=Decimal("0"))
        equity = self.balance + unrealized
        if equity > self.peak_equity:
            self.peak_equity = equity
        return AccountState(
            exchange_name=ExchangeName.BYBIT,
            execution_venue=ExecutionVenueKind.PAPER,
            equity=equity,
            available_balance=self.balance,
            wallet_balance=self.balance,
            margin_balance=self.balance,
            unrealized_pnl=unrealized,
            account_type="PAPER",
            position_mode=PositionMode.ONE_WAY,
            updated_at=as_of,
            raw_payload={"fees_paid": str(self.fees_paid)},
        )

    def pnl_snapshot(self, *, as_of: datetime) -> PnlSnapshot:
        account = self.account_state(as_of=as_of)
        drawdown = self.peak_equity - account.equity
        return PnlSnapshot(
            execution_venue=ExecutionVenueKind.PAPER,
            event_ts=as_of,
            equity=account.equity,
            balance=account.available_balance,
            realized_pnl=self.realized_pnl,
            unrealized_pnl=account.unrealized_pnl,
            drawdown=drawdown,
            payload={"fees_paid": str(self.fees_paid)},
        )

    def mark_to_market(self, *, symbol: str, snapshot: MarketSnapshot) -> None:
        position = self.positions.get(symbol)
        if position is None:
            return
        mark_price = self._mark_price(snapshot)
        if mark_price is None:
            return
        position.mark_price = mark_price
        position.last_price = mark_price
        position.unrealized_pnl = _unrealized(position.side, position.entry_price, mark_price, position.quantity)

    def apply_fill(self, *, fill: FillState, closed_reason: str | None = None) -> PositionState | None:
        self.balance -= fill.fee
        self.fees_paid += fill.fee

        position = self.positions.get(fill.symbol)
        direction = "long" if fill.side == "buy" else "short"
        remaining_quantity = fill.quantity
        changed_position: PositionState | None = None

        if position is not None and position.side != direction:
            close_quantity = min(position.quantity, remaining_quantity)
            realized = (
                (fill.price - position.entry_price) * close_quantity
                if position.side == "long"
                else (position.entry_price - fill.price) * close_quantity
            )
            self.realized_pnl += realized
            self.balance += realized
            position.quantity -= close_quantity
            position.realized_pnl += realized
            position.fees_paid += fill.fee
            position.last_price = fill.price
            position.mark_price = fill.price
            position.unrealized_pnl = (
                _unrealized(position.side, position.entry_price, fill.price, position.quantity)
                if position.quantity > 0
                else Decimal("0")
            )
            remaining_quantity -= close_quantity
            if position.quantity == 0:
                position.status = "closed"
                position.closed_reason = closed_reason
                position.closed_at = fill.filled_at
                position.updated_at = fill.filled_at
                changed_position = position.model_copy(deep=True)
                del self.positions[fill.symbol]
            else:
                position.updated_at = fill.filled_at
                changed_position = position.model_copy(deep=True)

        if remaining_quantity > 0:
            current = self.positions.get(fill.symbol)
            if current is None:
                current = PositionState(
                    exchange_name=ExchangeName.BYBIT,
                    execution_venue=ExecutionVenueKind.PAPER,
                    symbol=fill.symbol,
                    side=direction,
                    quantity=remaining_quantity,
                    entry_price=fill.price,
                    mark_price=fill.price,
                    last_price=fill.price,
                    fees_paid=fill.fee,
                    opened_at=fill.filled_at,
                    updated_at=fill.filled_at,
                    raw_payload={},
                )
                self.positions[fill.symbol] = current
            else:
                total_quantity = current.quantity + remaining_quantity
                current.entry_price = ((current.entry_price * current.quantity) + (fill.price * remaining_quantity)) / total_quantity
                current.quantity = total_quantity
                current.mark_price = fill.price
                current.last_price = fill.price
                current.fees_paid += fill.fee
                current.updated_at = fill.filled_at
            current.unrealized_pnl = _unrealized(current.side, current.entry_price, fill.price, current.quantity)
            changed_position = current.model_copy(deep=True)

        return changed_position

    def open_positions(self) -> list[PositionState]:
        return [position.model_copy(deep=True) for position in self.positions.values()]

    def _mark_price(self, snapshot: MarketSnapshot) -> Decimal | None:
        if snapshot.ticker is not None and snapshot.ticker.mark_price is not None:
            return snapshot.ticker.mark_price
        if snapshot.ticker is not None and snapshot.ticker.last_price is not None:
            return snapshot.ticker.last_price
        if snapshot.orderbook is not None and snapshot.orderbook.bids and snapshot.orderbook.asks:
            best_bid = snapshot.orderbook.bids[0].price
            best_ask = snapshot.orderbook.asks[0].price
            return (best_bid + best_ask) / Decimal("2")
        return None
