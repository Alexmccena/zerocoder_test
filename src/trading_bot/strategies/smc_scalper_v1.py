from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Callable

from trading_bot.config.schema import AppSettings
from trading_bot.domain.enums import EntryType, TradeAction
from trading_bot.domain.models import (
    FairValueGapZone,
    FeatureSnapshot,
    MarketSnapshot,
    OrderBlockZone,
    RuntimeState,
    TradeIntent,
)


def _apply_bps(price: Decimal, bps: float, *, direction: str) -> Decimal:
    multiplier = Decimal(str(bps)) / Decimal("10000")
    if direction == "up":
        return price * (Decimal("1") + multiplier)
    return price * (Decimal("1") - multiplier)


class SmcScalperV1Strategy:
    def __init__(self, *, config: AppSettings, runtime_state_provider: Callable[[], RuntimeState]) -> None:
        self.config = config
        self.runtime_state_provider = runtime_state_provider
        self._bar_index: dict[str, int] = defaultdict(int)
        self._pending_setup_state: dict[str, dict[str, object]] = {}
        self._open_setup_state: dict[str, dict[str, object]] = {}

    async def evaluate(self, snapshot: MarketSnapshot, features: FeatureSnapshot) -> list[TradeIntent]:
        symbol = snapshot.symbol
        self._bar_index[symbol] += 1

        runtime_state = self.runtime_state_provider()
        position = runtime_state.open_positions.get(symbol)
        has_open_order = any(order.symbol == symbol for order in runtime_state.open_orders.values())

        if position is None and not has_open_order:
            self._pending_setup_state.pop(symbol, None)
            self._open_setup_state.pop(symbol, None)
        if position is not None and symbol not in self._open_setup_state and symbol in self._pending_setup_state:
            self._open_setup_state[symbol] = self._pending_setup_state.pop(symbol)

        if not features.warmup_complete:
            return []

        long_setup = self._build_setup(snapshot=snapshot, features=features, side="long")
        short_setup = self._build_setup(snapshot=snapshot, features=features, side="short")

        if position is None:
            selected = self._choose_setup(long_setup, short_setup)
            if selected is None:
                return []
            intent = self._build_open_intent(snapshot=snapshot, setup=selected)
            self._pending_setup_state[symbol] = {
                **selected,
                "opened_bar_index": self._bar_index[symbol],
            }
            return [intent]

        setup_state = self._open_setup_state.get(symbol)
        if setup_state is None and position is not None:
            setup_state = {
                "side": position.side,
                "opened_bar_index": self._bar_index[symbol],
                "invalidation_price": position.entry_price,
                "zone_type": "unknown",
                "zone_bounds": {"lower": str(position.entry_price), "upper": str(position.entry_price)},
                "confirmations": [],
                "rule_trace": ["missing_setup_state_fallback"],
            }
            self._open_setup_state[symbol] = setup_state

        latest_kline = snapshot.closed_klines_by_interval.get(self.config.strategy.smc_scalper_v1.entry_timeframe)
        if latest_kline is None:
            return []

        if position.side == "long":
            if latest_kline.close_price < Decimal(str(setup_state["invalidation_price"])):
                return [self._build_close_intent(snapshot=snapshot, quantity=position.quantity, position_side="long", reason="invalidation")]
            if short_setup is not None:
                return [self._build_close_intent(snapshot=snapshot, quantity=position.quantity, position_side="long", reason="opposite_setup")]
            if self._bar_index[symbol] - int(setup_state["opened_bar_index"]) >= self.config.strategy.smc_scalper_v1.exit.max_hold_bars:
                return [self._build_close_intent(snapshot=snapshot, quantity=position.quantity, position_side="long", reason="max_hold")]
            return []

        if latest_kline.close_price > Decimal(str(setup_state["invalidation_price"])):
            return [self._build_close_intent(snapshot=snapshot, quantity=position.quantity, position_side="short", reason="invalidation")]
        if long_setup is not None:
            return [self._build_close_intent(snapshot=snapshot, quantity=position.quantity, position_side="short", reason="opposite_setup")]
        if self._bar_index[symbol] - int(setup_state["opened_bar_index"]) >= self.config.strategy.smc_scalper_v1.exit.max_hold_bars:
            return [self._build_close_intent(snapshot=snapshot, quantity=position.quantity, position_side="short", reason="max_hold")]
        return []

    def _build_setup(self, *, snapshot: MarketSnapshot, features: FeatureSnapshot, side: str) -> dict[str, object] | None:
        rule_trace: list[str] = []
        if snapshot.data_is_stale:
            rule_trace.append("stale_market_data")
            return None

        bias_ok = features.bias_state.state in ({"bullish", "neutral_bullish"} if side == "long" else {"bearish", "neutral_bearish"})
        if not bias_ok:
            rule_trace.append("bias_rejected")
            return None
        rule_trace.append("bias_ok")

        sweep_ok = features.sweep is not None and features.sweep.side == side and features.sweep.is_active
        if not sweep_ok:
            rule_trace.append("sweep_missing")
            return None
        rule_trace.append("sweep_ok")

        selected_zone = self._select_zone(snapshot=snapshot, side=side, fvgs=features.active_fvgs, order_blocks=features.active_order_blocks)
        if selected_zone is None:
            rule_trace.append("zone_missing")
            return None
        rule_trace.append(f"zone_ok:{selected_zone['zone_type']}")

        if side == "long" and features.funding_state.blocks_long:
            rule_trace.append("funding_blocked")
            return None
        if side == "short" and features.funding_state.blocks_short:
            rule_trace.append("funding_blocked")
            return None
        rule_trace.append("funding_ok")

        if not features.orderbook_state.has_fresh_orderbook:
            rule_trace.append("orderbook_stale")
            return None
        if not features.open_interest_state.available:
            rule_trace.append("oi_unavailable")
            return None
        rule_trace.append("market_context_ok")

        confirmations: list[str] = []
        if side == "long":
            if features.orderbook_state.supportive_long_imbalance:
                confirmations.append("imbalance")
            if features.open_interest_state.supportive_long:
                confirmations.append("open_interest")
            if features.orderbook_state.has_bid_wall:
                confirmations.append("wall")
        else:
            if features.orderbook_state.supportive_short_imbalance:
                confirmations.append("imbalance")
            if features.open_interest_state.supportive_short:
                confirmations.append("open_interest")
            if features.orderbook_state.has_ask_wall:
                confirmations.append("wall")
        if len(confirmations) < self.config.strategy.smc_scalper_v1.confirmations.min_support_count:
            rule_trace.append("confirmations_insufficient")
            return None
        rule_trace.append("confirmations_ok")

        invalidation = self._invalidation_price(
            side=side,
            zone_lower=Decimal(str(selected_zone["zone_bounds"]["lower"])),
            zone_upper=Decimal(str(selected_zone["zone_bounds"]["upper"])),
            sweep_level=features.sweep.swept_level,
        )
        return {
            "side": side,
            "bias_state": features.bias_state.state,
            "zone_type": selected_zone["zone_type"],
            "zone_bounds": selected_zone["zone_bounds"],
            "sweep_level": str(features.sweep.swept_level),
            "confirmations": confirmations,
            "rule_trace": rule_trace,
            "invalidation_price": str(invalidation),
            "selected_setup": {
                "side": side,
                "bias_state": features.bias_state.state,
                "zone_type": selected_zone["zone_type"],
                "zone_bounds": selected_zone["zone_bounds"],
                "sweep_level": str(features.sweep.swept_level),
                "confirmations": confirmations,
                "invalidation_price": str(invalidation),
            },
        }

    def _choose_setup(
        self,
        long_setup: dict[str, object] | None,
        short_setup: dict[str, object] | None,
    ) -> dict[str, object] | None:
        if long_setup is None:
            return short_setup
        if short_setup is None:
            return long_setup
        long_score = len(long_setup["confirmations"])  # type: ignore[arg-type]
        short_score = len(short_setup["confirmations"])  # type: ignore[arg-type]
        return long_setup if long_score >= short_score else short_setup

    def _select_zone(
        self,
        *,
        snapshot: MarketSnapshot,
        side: str,
        fvgs: list[FairValueGapZone],
        order_blocks: list[OrderBlockZone],
    ) -> dict[str, object] | None:
        latest_kline = snapshot.closed_klines_by_interval.get(self.config.strategy.smc_scalper_v1.entry_timeframe)
        if latest_kline is None:
            return None
        current_close = latest_kline.close_price
        touched_fvgs = [zone for zone in fvgs if zone.side == side and zone.touched]
        touched_obs = [zone for zone in order_blocks if zone.side == side and zone.touched]

        confluence: list[dict[str, object]] = []
        for fvg in touched_fvgs:
            for ob in touched_obs:
                lower = max(fvg.lower_bound, ob.lower_bound)
                upper = min(fvg.upper_bound, ob.upper_bound)
                if lower <= upper:
                    confluence.append(
                        {
                            "zone_type": "confluence",
                            "zone_bounds": {"lower": str(lower), "upper": str(upper)},
                            "created_at": max(fvg.created_at, ob.created_at),
                            "distance": abs(current_close - ((lower + upper) / Decimal("2"))),
                        }
                    )
        if confluence:
            best = sorted(confluence, key=lambda item: (-item["created_at"].timestamp(), item["distance"]))[0]
            return {
                "zone_type": best["zone_type"],
                "zone_bounds": best["zone_bounds"],
            }

        choices = [
            {
                "zone_type": "fvg",
                "zone_bounds": {"lower": str(zone.lower_bound), "upper": str(zone.upper_bound)},
                "created_at": zone.created_at,
                "distance": abs(current_close - ((zone.lower_bound + zone.upper_bound) / Decimal("2"))),
                "age_bars": zone.age_bars,
            }
            for zone in touched_fvgs
        ] + [
            {
                "zone_type": "order_block",
                "zone_bounds": {"lower": str(zone.lower_bound), "upper": str(zone.upper_bound)},
                "created_at": zone.created_at,
                "distance": abs(current_close - ((zone.lower_bound + zone.upper_bound) / Decimal("2"))),
                "age_bars": zone.age_bars,
            }
            for zone in touched_obs
        ]
        if not choices:
            return None
        best_choice = sorted(choices, key=lambda item: (item["age_bars"], item["distance"]))[0]
        return {
            "zone_type": best_choice["zone_type"],
            "zone_bounds": best_choice["zone_bounds"],
        }

    def _invalidation_price(self, *, side: str, zone_lower: Decimal, zone_upper: Decimal, sweep_level: Decimal) -> Decimal:
        buffer_bps = self.config.strategy.smc_scalper_v1.exit.invalidation_buffer_bps
        if side == "long":
            base = min(sweep_level, zone_lower)
            return _apply_bps(base, buffer_bps, direction="down")
        base = max(sweep_level, zone_upper)
        return _apply_bps(base, buffer_bps, direction="up")

    def _build_open_intent(self, *, snapshot: MarketSnapshot, setup: dict[str, object]) -> TradeIntent:
        reference_price = self._reference_price(snapshot)
        quantity = self.config.paper.default_order_notional_usdt / reference_price
        action = TradeAction.OPEN_LONG if setup["side"] == "long" else TradeAction.OPEN_SHORT
        side = "buy" if action == TradeAction.OPEN_LONG else "sell"
        return TradeIntent(
            strategy_name=self.config.strategy.name,
            action=action,
            symbol=snapshot.symbol,
            side=side,
            entry_type=EntryType.MARKET,
            quantity=quantity,
            reference_price=reference_price,
            ttl_ms=self.config.execution.limit_ttl_ms,
            metadata={
                "setup_side": setup["side"],
                "bias_state": setup["bias_state"],
                "zone_type": setup["zone_type"],
                "zone_bounds": setup["zone_bounds"],
                "sweep_level": setup["sweep_level"],
                "confirmations": setup["confirmations"],
                "selected_setup": setup["selected_setup"],
                "rule_trace": setup["rule_trace"],
                "setup_context": {"strategy": self.config.strategy.name},
            },
            generated_at=snapshot.as_of,
        )

    def _build_close_intent(
        self,
        *,
        snapshot: MarketSnapshot,
        quantity: Decimal,
        position_side: str,
        reason: str,
    ) -> TradeIntent:
        action = TradeAction.CLOSE_LONG if position_side == "long" else TradeAction.CLOSE_SHORT
        side = "sell" if position_side == "long" else "buy"
        return TradeIntent(
            strategy_name=self.config.strategy.name,
            action=action,
            symbol=snapshot.symbol,
            side=side,
            entry_type=EntryType.MARKET,
            quantity=quantity,
            reference_price=self._reference_price(snapshot),
            ttl_ms=self.config.execution.limit_ttl_ms,
            metadata={"close_reason": reason},
            generated_at=snapshot.as_of,
        )

    def _reference_price(self, snapshot: MarketSnapshot) -> Decimal:
        if snapshot.ticker is not None and snapshot.ticker.last_price is not None:
            return snapshot.ticker.last_price
        if snapshot.orderbook is not None and snapshot.orderbook.bids and snapshot.orderbook.asks:
            return (snapshot.orderbook.bids[0].price + snapshot.orderbook.asks[0].price) / Decimal("2")
        latest = snapshot.closed_klines_by_interval.get(self.config.strategy.smc_scalper_v1.entry_timeframe)
        if latest is not None:
            return latest.close_price
        raise RuntimeError(f"missing reference price for {snapshot.symbol}")
