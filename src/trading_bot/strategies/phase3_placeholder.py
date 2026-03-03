from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Callable

from trading_bot.config.schema import AppSettings
from trading_bot.domain.enums import EntryType, TradeAction
from trading_bot.domain.models import FeatureSnapshot, MarketSnapshot, RuntimeState, TradeIntent


class Phase3PlaceholderStrategy:
    def __init__(self, *, config: AppSettings, runtime_state_provider: Callable[[], RuntimeState]) -> None:
        self.config = config
        self.runtime_state_provider = runtime_state_provider
        self._hold_counts: dict[str, int] = defaultdict(int)

    async def evaluate(self, snapshot: MarketSnapshot, features: FeatureSnapshot) -> list[TradeIntent]:
        position = self.runtime_state_provider().open_positions.get(snapshot.symbol)
        bullish = (
            features.last_close_change_bps >= Decimal(str(self.config.strategy.placeholder_signal_threshold_bps))
            and features.top5_imbalance >= self.config.strategy.placeholder_min_imbalance
        )
        bearish = (
            features.last_close_change_bps <= -Decimal(str(self.config.strategy.placeholder_signal_threshold_bps))
            and features.top5_imbalance <= -self.config.strategy.placeholder_min_imbalance
        )
        if position is None:
            self._hold_counts[snapshot.symbol] = 0
            if snapshot.data_is_stale or not features.has_fresh_orderbook:
                return []
            if bullish:
                return [self._build_open_intent(snapshot=snapshot, action=TradeAction.OPEN_LONG)]
            if bearish:
                return [self._build_open_intent(snapshot=snapshot, action=TradeAction.OPEN_SHORT)]
            return []

        self._hold_counts[snapshot.symbol] += 1
        if position.side == "long" and bearish:
            return [
                self._build_close_intent(
                    snapshot=snapshot,
                    position_side="long",
                    quantity=position.quantity,
                    reason="opposite_signal",
                )
            ]
        if position.side == "short" and bullish:
            return [
                self._build_close_intent(
                    snapshot=snapshot,
                    position_side="short",
                    quantity=position.quantity,
                    reason="opposite_signal",
                )
            ]
        if self._hold_counts[snapshot.symbol] >= self.config.strategy.placeholder_max_hold_closed_klines:
            return [
                self._build_close_intent(
                    snapshot=snapshot,
                    position_side=position.side,
                    quantity=position.quantity,
                    reason="max_hold",
                )
            ]
        return []

    def _build_open_intent(self, *, snapshot: MarketSnapshot, action: TradeAction) -> TradeIntent:
        reference_price = self._reference_price(snapshot)
        quantity = self.config.paper.default_order_notional_usdt / reference_price
        side = "buy" if action == TradeAction.OPEN_LONG else "sell"
        entry_type = self.config.execution.default_entry_type
        return TradeIntent(
            strategy_name=self.config.strategy.name,
            action=action,
            symbol=snapshot.symbol,
            side=side,
            entry_type=entry_type,
            quantity=quantity,
            reference_price=reference_price,
            limit_price=self._limit_price(snapshot, side=side, entry_type=entry_type),
            ttl_ms=self.config.execution.limit_ttl_ms,
            metadata={},
            generated_at=snapshot.as_of,
        )

    def _build_close_intent(
        self,
        *,
        snapshot: MarketSnapshot,
        position_side: str,
        quantity: Decimal,
        reason: str,
    ) -> TradeIntent:
        action = TradeAction.CLOSE_LONG if position_side == "long" else TradeAction.CLOSE_SHORT
        side = "sell" if position_side == "long" else "buy"
        entry_type = self.config.execution.default_entry_type
        return TradeIntent(
            strategy_name=self.config.strategy.name,
            action=action,
            symbol=snapshot.symbol,
            side=side,
            entry_type=entry_type,
            quantity=quantity,
            reference_price=self._reference_price(snapshot),
            limit_price=self._limit_price(snapshot, side=side, entry_type=entry_type),
            ttl_ms=self.config.execution.limit_ttl_ms,
            metadata={"close_reason": reason},
            generated_at=snapshot.as_of,
        )

    def _reference_price(self, snapshot: MarketSnapshot) -> Decimal:
        if snapshot.ticker is not None and snapshot.ticker.last_price is not None:
            return snapshot.ticker.last_price
        if snapshot.orderbook is not None and snapshot.orderbook.bids and snapshot.orderbook.asks:
            return (snapshot.orderbook.bids[0].price + snapshot.orderbook.asks[0].price) / Decimal("2")
        latest = snapshot.closed_klines_by_interval.get(self.config.strategy.default_timeframe)
        if latest is not None:
            return latest.close_price
        raise RuntimeError(f"missing reference price for {snapshot.symbol}")

    def _limit_price(self, snapshot: MarketSnapshot, *, side: str, entry_type: EntryType) -> Decimal | None:
        if entry_type != EntryType.LIMIT or snapshot.orderbook is None:
            return None
        if side == "buy" and snapshot.orderbook.bids:
            return snapshot.orderbook.bids[0].price
        if side == "sell" and snapshot.orderbook.asks:
            return snapshot.orderbook.asks[0].price
        return self._reference_price(snapshot)
