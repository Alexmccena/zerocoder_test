from __future__ import annotations

from decimal import Decimal
from typing import Any

from trading_bot.domain.models import AccountState, PnlSnapshot


def build_runtime_summary(
    *,
    initial_equity: Decimal,
    account_state: AccountState | None,
    pnl_snapshot: PnlSnapshot | None,
    total_signals: int,
    total_orders: int,
    total_fills: int,
) -> dict[str, Any]:
    equity = account_state.equity if account_state is not None else initial_equity
    balance = account_state.available_balance if account_state is not None else initial_equity
    realized = pnl_snapshot.realized_pnl if pnl_snapshot is not None else Decimal("0")
    unrealized = pnl_snapshot.unrealized_pnl if pnl_snapshot is not None else Decimal("0")
    drawdown = pnl_snapshot.drawdown if pnl_snapshot is not None else Decimal("0")
    return {
        "initial_equity": str(initial_equity),
        "final_equity": str(equity),
        "balance": str(balance),
        "net_pnl": str(equity - initial_equity),
        "realized_pnl": str(realized),
        "unrealized_pnl": str(unrealized),
        "max_drawdown": str(drawdown),
        "total_signals": total_signals,
        "total_orders": total_orders,
        "total_fills": total_fills,
    }
