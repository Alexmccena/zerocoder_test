from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from trading_bot.config.schema import ExecutionConfig, PaperConfig
from trading_bot.domain.models import FillState, MarketSnapshot, OrderState


def _bps(value: Decimal) -> Decimal:
    return value * Decimal("10000")


@dataclass(slots=True)
class FillAttempt:
    fill: FillState | None
    reason: str | None = None


class PaperFillModel:
    def __init__(self, *, execution: ExecutionConfig, paper: PaperConfig) -> None:
        self.execution = execution
        self.paper = paper

    def simulate_market_fill(self, *, order: OrderState, snapshot: MarketSnapshot, as_of: datetime) -> FillAttempt:
        if snapshot.orderbook is None:
            return FillAttempt(fill=None, reason="missing_orderbook")
        if self._is_stale(snapshot, as_of):
            return FillAttempt(fill=None, reason="stale_market_data")

        levels = snapshot.orderbook.asks if order.side == "buy" else snapshot.orderbook.bids
        if not levels:
            return FillAttempt(fill=None, reason="missing_orderbook")

        reference_price = levels[0].price
        remaining = order.quantity - order.filled_quantity
        if remaining <= 0:
            return FillAttempt(fill=None, reason="empty_order")

        total_value = Decimal("0")
        filled_quantity = Decimal("0")
        for level in levels:
            take = min(level.size, remaining - filled_quantity)
            if take <= 0:
                continue
            total_value += level.price * take
            filled_quantity += take
            if filled_quantity >= remaining:
                break
        if filled_quantity < remaining:
            return FillAttempt(fill=None, reason="insufficient_depth")

        average_price = total_value / filled_quantity
        slippage_bps = self._market_slippage_bps(order.side, reference_price, average_price)
        if slippage_bps > Decimal(str(self.execution.market_slippage_guard_bps)):
            return FillAttempt(fill=None, reason="slippage_guard")

        fee_rate = Decimal(str(self.paper.taker_fee_bps)) / Decimal("10000")
        fee = average_price * filled_quantity * fee_rate
        return FillAttempt(
            fill=FillState(
                order_id=order.order_id,
                exchange_name=order.exchange_name,
                execution_venue=order.execution_venue,
                symbol=order.symbol,
                side=order.side,
                price=average_price,
                quantity=filled_quantity,
                fee=fee,
                fee_asset="USDT",
                liquidity_type="taker",
                is_maker=False,
                slippage_bps=slippage_bps,
                filled_at=as_of,
            )
        )

    def simulate_limit_fill(self, *, order: OrderState, snapshot: MarketSnapshot, as_of: datetime) -> FillAttempt:
        if order.price is None or snapshot.orderbook is None:
            return FillAttempt(fill=None)
        if self._is_stale(snapshot, as_of):
            return FillAttempt(fill=None)
        if order.expires_at is not None and as_of >= order.expires_at:
            return FillAttempt(fill=None, reason="expired")

        remaining = order.quantity - order.filled_quantity
        if remaining <= 0:
            return FillAttempt(fill=None)

        best_level = snapshot.orderbook.asks[0] if order.side == "buy" and snapshot.orderbook.asks else None
        if order.side == "sell" and snapshot.orderbook.bids:
            best_level = snapshot.orderbook.bids[0]
        if best_level is None:
            return FillAttempt(fill=None)

        touched = best_level.price <= order.price if order.side == "buy" else best_level.price >= order.price
        if not touched:
            return FillAttempt(fill=None)

        visible_quantity = best_level.size * Decimal(str(self.paper.limit_fill_visible_ratio))
        if visible_quantity <= 0:
            return FillAttempt(fill=None)
        if not self.paper.allow_partial_limit_fills and visible_quantity < remaining:
            return FillAttempt(fill=None)

        fill_quantity = min(remaining, visible_quantity)
        fill_price = min(best_level.price, order.price) if order.side == "buy" else max(best_level.price, order.price)
        fee_rate = Decimal(str(self.paper.maker_fee_bps)) / Decimal("10000")
        fee = fill_price * fill_quantity * fee_rate
        return FillAttempt(
            fill=FillState(
                order_id=order.order_id,
                exchange_name=order.exchange_name,
                execution_venue=order.execution_venue,
                symbol=order.symbol,
                side=order.side,
                price=fill_price,
                quantity=fill_quantity,
                fee=fee,
                fee_asset="USDT",
                liquidity_type="maker",
                is_maker=True,
                slippage_bps=Decimal("0"),
                filled_at=as_of,
            )
        )

    def _is_stale(self, snapshot: MarketSnapshot, as_of: datetime) -> bool:
        orderbook = snapshot.orderbook
        if orderbook is None:
            return True
        age_ms = (as_of - orderbook.event_ts).total_seconds() * 1000
        return age_ms > self.execution.max_market_data_age_ms

    def _market_slippage_bps(self, side: str, reference_price: Decimal, average_price: Decimal) -> Decimal:
        if reference_price == 0:
            return Decimal("0")
        if side == "buy":
            return _bps((average_price - reference_price) / reference_price)
        return _bps((reference_price - average_price) / reference_price)
