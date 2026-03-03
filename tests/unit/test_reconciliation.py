from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from trading_bot.domain.enums import ExchangeName, ExecutionVenueKind, PositionMode, RunMode
from trading_bot.domain.models import (
    AccountState,
    BracketState,
    ExecutionResult,
    OrderState,
    PositionState,
    VenueStateSnapshot,
)
from trading_bot.runtime.reconciliation import RuntimeReconciler
from trading_bot.runtime.state import RuntimeStateStore


def _account() -> AccountState:
    return AccountState(
        exchange_name=ExchangeName.BYBIT,
        execution_venue=ExecutionVenueKind.PAPER,
        equity=Decimal("10"),
        available_balance=Decimal("10"),
        wallet_balance=Decimal("10"),
        margin_balance=Decimal("10"),
        position_mode=PositionMode.ONE_WAY,
    )


def _position() -> PositionState:
    return PositionState(
        exchange_name=ExchangeName.BYBIT,
        execution_venue=ExecutionVenueKind.PAPER,
        symbol="BTCUSDT",
        side="long",
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
    )


class FakeExecutionEngine:
    def __init__(self, *, snapshot_state: VenueStateSnapshot, brackets: dict[str, BracketState]) -> None:
        self._snapshot_state = snapshot_state
        self._brackets = brackets
        self.rearmed: list[str] = []
        self.cancelled: list[str] = []

    async def snapshot_state(self) -> VenueStateSnapshot:
        return self._snapshot_state

    def active_brackets(self) -> dict[str, BracketState]:
        return {symbol: bracket.model_copy(deep=True) for symbol, bracket in self._brackets.items()}

    async def rearm_bracket(self, symbol: str, *, as_of: datetime) -> ExecutionResult:
        self.rearmed.append(symbol)
        return ExecutionResult(
            accepted=True,
            orders=[
                OrderState(
                    order_id="sl-1",
                    exchange_name=ExchangeName.BYBIT,
                    execution_venue=ExecutionVenueKind.PAPER,
                    symbol=symbol,
                    side="sell",
                    order_type="stop_market",
                    status="working",
                    quantity=Decimal("1"),
                    stop_price=Decimal("99"),
                    reduce_only=True,
                    intent_id="intent-1",
                    raw_payload={"order_role": "stop_loss"},
                ),
                OrderState(
                    order_id="tp-1",
                    exchange_name=ExchangeName.BYBIT,
                    execution_venue=ExecutionVenueKind.PAPER,
                    symbol=symbol,
                    side="sell",
                    order_type="limit",
                    status="working",
                    quantity=Decimal("1"),
                    price=Decimal("102"),
                    reduce_only=True,
                    intent_id="intent-1",
                    raw_payload={"order_role": "take_profit"},
                ),
            ],
        )

    async def cancel_order(self, order_id: str, *, as_of: datetime) -> ExecutionResult:
        self.cancelled.append(order_id)
        return ExecutionResult(
            accepted=True,
            orders=[
                OrderState(
                    order_id=order_id,
                    exchange_name=ExchangeName.BYBIT,
                    execution_venue=ExecutionVenueKind.PAPER,
                    symbol="BTCUSDT",
                    side="sell",
                    order_type="limit",
                    status="cancelled",
                    quantity=Decimal("1"),
                )
            ],
        )


async def test_reconciliation_rearms_missing_protection() -> None:
    state = RuntimeStateStore(run_mode=RunMode.PAPER, execution_venue=ExecutionVenueKind.PAPER)
    state.set_account(_account())
    state.update_position(_position())
    engine = FakeExecutionEngine(
        snapshot_state=VenueStateSnapshot(account_state=_account(), open_orders=[], open_positions=[_position()]),
        brackets={
            "BTCUSDT": BracketState(
                symbol="BTCUSDT",
                intent_id="intent-1",
                side="long",
                quantity=Decimal("1"),
                stop_loss_price=Decimal("99"),
                take_profit_price=Decimal("102"),
            )
        },
    )

    result = await RuntimeReconciler(execution_engine=engine).reconcile(state=state, as_of=datetime.now(timezone.utc))

    assert engine.rearmed == ["BTCUSDT"]
    assert [order.raw_payload["order_role"] for order in result.orders] == ["stop_loss", "take_profit"]


async def test_reconciliation_closes_runtime_drift_and_cancels_dangling_bracket() -> None:
    state = RuntimeStateStore(run_mode=RunMode.PAPER, execution_venue=ExecutionVenueKind.PAPER)
    state.set_account(_account())
    state.update_position(_position())
    engine = FakeExecutionEngine(
        snapshot_state=VenueStateSnapshot(account_state=_account(), open_orders=[], open_positions=[]),
        brackets={
            "BTCUSDT": BracketState(
                symbol="BTCUSDT",
                intent_id="intent-1",
                side="long",
                quantity=Decimal("1"),
                stop_loss_price=Decimal("99"),
                take_profit_price=Decimal("102"),
                stop_loss_order_id="sl-1",
                take_profit_order_id="tp-1",
            )
        },
    )

    result = await RuntimeReconciler(execution_engine=engine).reconcile(state=state, as_of=datetime.now(timezone.utc))

    assert len(result.positions) == 1
    assert result.positions[0].closed_reason == "drift_closed"
    assert engine.cancelled == ["sl-1", "tp-1"]
