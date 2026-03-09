from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from trading_bot.config.schema import AppSettings, GridPairConfig
from trading_bot.domain.models import FeatureSnapshot, MarketSnapshot, RuntimeState, TradeIntent


@dataclass(frozen=True, slots=True)
class GridLevel:
    level_index: int
    price: Decimal
    quantity: Decimal
    notional_quote: Decimal


class GridDcaV1Strategy:
    """
    Stateful grid strategy helper.

    Runtime orchestration is delegated to runtime/grid_runtime.py; this class keeps
    pure calculation logic and satisfies the Strategy protocol.
    """

    def __init__(self, *, config: AppSettings, runtime_state_provider: Callable[[], RuntimeState]) -> None:
        self.config = config
        self.runtime_state_provider = runtime_state_provider

    async def evaluate(self, snapshot: MarketSnapshot, features: FeatureSnapshot) -> list[TradeIntent]:
        del snapshot, features
        # Grid runtime handles orchestration directly.
        return []

    def find_pair(self, symbol: str) -> GridPairConfig | None:
        for pair in self.config.strategy.grid_dca_v1.pairs:
            if pair.symbol == symbol:
                return pair
        return None

    def build_buy_levels(self, *, pair: GridPairConfig, anchor_price: Decimal) -> list[GridLevel]:
        if anchor_price <= 0:
            return []
        corridor = Decimal(str(pair.corridor_pct)) / Decimal("100")
        if corridor <= 0:
            return []
        levels: list[GridLevel] = []
        # stack_size_quote is interpreted as notional (already leverage-adjusted for futures).
        per_order_quote = pair.stack_size_quote / Decimal(pair.orders_per_stack)
        step = corridor / Decimal(pair.orders_per_stack)
        for index in range(pair.orders_per_stack):
            ratio = Decimal("1") - (step * Decimal(index + 1))
            if ratio <= 0:
                continue
            price = anchor_price * ratio
            if price <= 0:
                continue
            quantity = per_order_quote / price
            levels.append(
                GridLevel(
                    level_index=index + 1,
                    price=price,
                    quantity=quantity,
                    notional_quote=per_order_quote,
                )
            )
        return levels
