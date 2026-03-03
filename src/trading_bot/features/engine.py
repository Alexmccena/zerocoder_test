from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from statistics import median

from trading_bot.config.schema import AppSettings
from trading_bot.domain.models import (
    BiasState,
    FairValueGapZone,
    FeatureSnapshot,
    FundingFeatureState,
    LiquiditySweepState,
    LiquidationFeatureState,
    MarketSnapshot,
    OpenInterestFeatureState,
    OrderBlockZone,
    OrderBookFeatureState,
    SetupCandidate,
    StructureState,
)
from trading_bot.marketdata.events import (
    FundingRateEvent,
    KlineEvent,
    LiquidationEvent,
    MarketEvent,
    OpenInterestEvent,
    OrderBookEvent,
)
from trading_bot.timeframes import canonicalize_interval, interval_to_minutes


def _bps_change(current: Decimal, reference: Decimal) -> Decimal:
    if reference == 0:
        return Decimal("0")
    return ((current - reference) / reference) * Decimal("10000")


def _threshold_ratio(bps: float) -> Decimal:
    return Decimal("1") + (Decimal(str(bps)) / Decimal("10000"))


def _threshold_ratio_down(bps: float) -> Decimal:
    return Decimal("1") - (Decimal(str(bps)) / Decimal("10000"))


def _touches_zone(kline: KlineEvent, *, lower: Decimal, upper: Decimal) -> bool:
    return kline.low_price <= upper and kline.high_price >= lower


def _mid_price(snapshot: MarketSnapshot) -> Decimal | None:
    if snapshot.orderbook is not None and snapshot.orderbook.bids and snapshot.orderbook.asks:
        return (snapshot.orderbook.bids[0].price + snapshot.orderbook.asks[0].price) / Decimal("2")
    if snapshot.ticker is not None and snapshot.ticker.last_price is not None:
        return snapshot.ticker.last_price
    return None


@dataclass(slots=True)
class _StructureEventInfo:
    event_type: str
    index: int
    break_price: Decimal
    pivot_high: Decimal | None
    pivot_low: Decimal | None
    ended_at: datetime


@dataclass(slots=True)
class _StructureAnalysis:
    state: StructureState
    events: list[_StructureEventInfo]


@dataclass(slots=True)
class _SymbolFeatureState:
    klines: dict[str, deque[KlineEvent]]
    orderbooks: deque[OrderBookEvent]
    open_interest: deque[OpenInterestEvent]
    liquidations: deque[LiquidationEvent]
    funding_rate: FundingRateEvent | None = None


class FeatureProvider:
    def __init__(self, *, config: AppSettings | None = None, timeframe: str | None = None) -> None:
        self.config = config
        self.default_timeframe = canonicalize_interval(
            timeframe or (config.strategy.default_timeframe if config is not None else "1m")
        )
        self.strategy_name = config.strategy.name if config is not None else "phase3_placeholder"

        smc = config.strategy.smc_scalper_v1 if config is not None else None
        self.bias_timeframe = smc.bias_timeframe if smc is not None else "15m"
        self.structure_timeframe = smc.structure_timeframe if smc is not None else "5m"
        self.entry_timeframe = smc.entry_timeframe if smc is not None else self.default_timeframe
        self.entry_bar_limit = smc.history.entry_bars if smc is not None else 160
        self.structure_bar_limit = smc.history.structure_bars if smc is not None else 96
        self.bias_bar_limit = smc.history.bias_bars if smc is not None else 64
        self.orderbook_limit = smc.history.orderbook_snapshots if smc is not None else 120
        self.oi_limit = max(smc.history.oi_points, smc.open_interest.lookback_points + 1) if smc is not None else 32
        self.liquidation_limit = smc.history.liquidation_events if smc is not None else 200
        self._state: dict[str, _SymbolFeatureState] = defaultdict(self._build_state)

    def observe(self, event: MarketEvent, snapshot: MarketSnapshot) -> None:  # noqa: ARG002
        state = self._state[event.symbol]
        if isinstance(event, KlineEvent) and event.is_closed:
            interval = canonicalize_interval(event.interval)
            history = state.klines.setdefault(interval, deque(maxlen=self._kline_limit(interval)))
            self._append_unique_kline(history, event.model_copy(update={"interval": interval}))
        elif isinstance(event, OrderBookEvent):
            self._append_or_replace(state.orderbooks, event, key=lambda item: (item.event_ts, item.sequence))
        elif isinstance(event, OpenInterestEvent):
            self._append_or_replace(state.open_interest, event, key=lambda item: item.event_ts)
        elif isinstance(event, LiquidationEvent):
            self._append_or_replace(state.liquidations, event, key=lambda item: (item.event_ts, item.side, item.price))
        elif isinstance(event, FundingRateEvent):
            state.funding_rate = event

    def compute(self, snapshot: MarketSnapshot) -> FeatureSnapshot:
        state = self._state[snapshot.symbol]
        entry_klines = list(state.klines.get(self.entry_timeframe, ()))
        structure_klines = list(state.klines.get(self.structure_timeframe, ()))
        bias_klines = list(state.klines.get(self.bias_timeframe, ()))

        orderbook_state = self._compute_orderbook_state(snapshot, state.orderbooks)
        oi_state = self._compute_open_interest_state(state.open_interest)
        funding_state = self._compute_funding_state(snapshot, state.funding_rate)
        liquidation_state = self._compute_liquidation_state(snapshot, state.liquidations)

        min_break_bps = self._smc_config.structure.min_break_bps if self._has_smc_config else 2.0
        max_signal_age_bars = self._smc_config.structure.max_signal_age_bars if self._has_smc_config else 6
        swing_lookback = self._smc_config.structure.swing_lookback_bars if self._has_smc_config else 2

        bias_analysis = self._analyze_structure(
            bias_klines,
            timeframe=self.bias_timeframe,
            min_break_bps=min_break_bps,
            max_signal_age_bars=max_signal_age_bars,
            swing_lookback=swing_lookback,
        )
        structure_analysis = self._analyze_structure(
            structure_klines,
            timeframe=self.structure_timeframe,
            min_break_bps=min_break_bps,
            max_signal_age_bars=max_signal_age_bars,
            swing_lookback=swing_lookback,
        )
        entry_analysis = self._analyze_structure(
            entry_klines,
            timeframe=self.entry_timeframe,
            min_break_bps=min_break_bps,
            max_signal_age_bars=max_signal_age_bars,
            swing_lookback=swing_lookback,
        )

        sweep = self._compute_sweep(entry_klines)
        active_fvgs = self._compute_fvgs(entry_klines)
        active_order_blocks = self._compute_order_blocks(entry_klines, entry_analysis.events)
        setup_candidates = self._build_setup_candidates(active_fvgs, active_order_blocks)

        features = FeatureSnapshot(
            symbol=snapshot.symbol,
            last_close_change_bps=self._compute_last_close_change_bps(entry_klines),
            top5_imbalance=orderbook_state.imbalance,
            open_interest_delta=oi_state.delta_bps,
            funding_rate=funding_state.funding_rate,
            has_fresh_orderbook=orderbook_state.has_fresh_orderbook,
            bias_state=self._build_bias_state(bias_analysis),
            structure_state=structure_analysis.state,
            entry_structure_state=entry_analysis.state,
            sweep=sweep,
            active_fvgs=active_fvgs,
            active_order_blocks=active_order_blocks,
            orderbook_state=orderbook_state,
            open_interest_state=oi_state,
            funding_state=funding_state,
            liquidation_state=liquidation_state,
            setup_candidates=setup_candidates,
            warmup_complete=self._is_warm(snapshot, state),
        )
        features.payload = features.model_dump(mode="json", exclude={"payload"})
        return features

    def required_warmup_minutes(self) -> int:
        if not self._has_smc_config:
            return 0
        return max(
            self._smc_config.history.entry_bars * interval_to_minutes(self.entry_timeframe),
            self._smc_config.history.structure_bars * interval_to_minutes(self.structure_timeframe),
            self._smc_config.history.bias_bars * interval_to_minutes(self.bias_timeframe),
        )

    @property
    def _has_smc_config(self) -> bool:
        return self.config is not None and self.strategy_name == "smc_scalper_v1"

    @property
    def _smc_config(self):
        if self.config is None:
            raise RuntimeError("SMC config is unavailable without AppSettings")
        return self.config.strategy.smc_scalper_v1

    def _build_state(self) -> _SymbolFeatureState:
        return _SymbolFeatureState(
            klines={},
            orderbooks=deque(maxlen=self.orderbook_limit),
            open_interest=deque(maxlen=self.oi_limit),
            liquidations=deque(maxlen=self.liquidation_limit),
        )

    def _kline_limit(self, interval: str) -> int:
        if interval == self.entry_timeframe:
            return self.entry_bar_limit
        if interval == self.structure_timeframe:
            return self.structure_bar_limit
        if interval == self.bias_timeframe:
            return self.bias_bar_limit
        return max(self.entry_bar_limit, self.structure_bar_limit, self.bias_bar_limit)

    def _append_unique_kline(self, history: deque[KlineEvent], event: KlineEvent) -> None:
        if history and history[-1].end_at == event.end_at:
            history[-1] = event
            return
        history.append(event)

    def _append_or_replace(self, history: deque, event, *, key) -> None:
        if history and key(history[-1]) == key(event):
            history[-1] = event
            return
        history.append(event)

    def _is_warm(self, snapshot: MarketSnapshot, state: _SymbolFeatureState) -> bool:
        if snapshot.instrument is None:
            return False
        if self.strategy_name != "smc_scalper_v1":
            return self.default_timeframe in snapshot.closed_klines_by_interval
        return (
            len(state.klines.get(self.entry_timeframe, ())) >= self.entry_bar_limit
            and len(state.klines.get(self.structure_timeframe, ())) >= self.structure_bar_limit
            and len(state.klines.get(self.bias_timeframe, ())) >= self.bias_bar_limit
            and len(state.open_interest) > self._smc_config.open_interest.lookback_points
            and len(state.orderbooks) >= self._smc_config.orderbook.wall_min_persistence_snapshots
        )

    def _compute_last_close_change_bps(self, klines: list[KlineEvent]) -> Decimal:
        if len(klines) < 2:
            return Decimal("0")
        return _bps_change(klines[-1].close_price, klines[-2].close_price)

    def _build_bias_state(self, analysis: _StructureAnalysis) -> BiasState:
        state = "neutral"
        if analysis.state.is_active:
            mapping = {
                "bos_up": "bullish",
                "choch_up": "neutral_bullish",
                "bos_down": "bearish",
                "choch_down": "neutral_bearish",
            }
            state = mapping.get(analysis.state.event_type or "", "neutral")
        return BiasState(
            timeframe=analysis.state.timeframe,
            state=state,
            event_type=analysis.state.event_type,
            direction=analysis.state.direction,
            age_bars=analysis.state.age_bars,
            last_event_at=analysis.state.last_event_at,
        )

    def _analyze_structure(
        self,
        klines: list[KlineEvent],
        *,
        timeframe: str,
        min_break_bps: float,
        max_signal_age_bars: int,
        swing_lookback: int,
    ) -> _StructureAnalysis:
        if len(klines) < (swing_lookback * 2) + 3:
            return _StructureAnalysis(state=StructureState(timeframe=timeframe), events=[])

        pivot_highs: list[tuple[int, Decimal]] = []
        pivot_lows: list[tuple[int, Decimal]] = []
        for index in range(swing_lookback, len(klines) - swing_lookback):
            current = klines[index]
            left = klines[index - swing_lookback : index]
            right = klines[index + 1 : index + swing_lookback + 1]
            if all(current.high_price > item.high_price for item in left + right):
                pivot_highs.append((index, current.high_price))
            if all(current.low_price < item.low_price for item in left + right):
                pivot_lows.append((index, current.low_price))

        events: list[_StructureEventInfo] = []
        direction = "neutral"
        for index, kline in enumerate(klines):
            last_high = next((item for item in reversed(pivot_highs) if item[0] < index), None)
            last_low = next((item for item in reversed(pivot_lows) if item[0] < index), None)
            if last_high is not None and kline.close_price >= last_high[1] * _threshold_ratio(min_break_bps):
                event_type = "choch_up" if direction == "bearish" else "bos_up"
                direction = "bullish"
                events.append(
                    _StructureEventInfo(
                        event_type=event_type,
                        index=index,
                        break_price=kline.close_price,
                        pivot_high=last_high[1],
                        pivot_low=last_low[1] if last_low is not None else None,
                        ended_at=kline.end_at,
                    )
                )
            elif last_low is not None and kline.close_price <= last_low[1] * _threshold_ratio_down(min_break_bps):
                event_type = "choch_down" if direction == "bullish" else "bos_down"
                direction = "bearish"
                events.append(
                    _StructureEventInfo(
                        event_type=event_type,
                        index=index,
                        break_price=kline.close_price,
                        pivot_high=last_high[1] if last_high is not None else None,
                        pivot_low=last_low[1],
                        ended_at=kline.end_at,
                    )
                )

        last_event = events[-1] if events else None
        age_bars = (len(klines) - 1 - last_event.index) if last_event is not None else None
        is_active = age_bars is not None and age_bars <= max_signal_age_bars
        return _StructureAnalysis(
            state=StructureState(
                timeframe=timeframe,
                direction=direction if is_active else "neutral",
                event_type=last_event.event_type if last_event is not None and is_active else None,
                pivot_high=last_event.pivot_high if last_event is not None else None,
                pivot_low=last_event.pivot_low if last_event is not None else None,
                break_price=last_event.break_price if last_event is not None else None,
                age_bars=age_bars,
                last_event_at=last_event.ended_at if last_event is not None else None,
                is_active=is_active,
            ),
            events=events,
        )

    def _compute_sweep(self, klines: list[KlineEvent]) -> LiquiditySweepState | None:
        if not self._has_smc_config or len(klines) < self._smc_config.sweep.lookback_bars + 2:
            return None
        long_sweep = self._find_long_sweep(klines)
        short_sweep = self._find_short_sweep(klines)
        candidates = [candidate for candidate in [long_sweep, short_sweep] if candidate is not None]
        if not candidates:
            return None
        return max(candidates, key=lambda candidate: candidate.reclaim_at)

    def _find_long_sweep(self, klines: list[KlineEvent]) -> LiquiditySweepState | None:
        config = self._smc_config.sweep
        last_index = len(klines) - 1
        for index in range(last_index, config.lookback_bars - 1, -1):
            window = klines[index - config.lookback_bars : index]
            if not window:
                continue
            swept_level = min(item.low_price for item in window)
            if klines[index].low_price > swept_level * _threshold_ratio_down(config.min_penetration_bps):
                continue
            reclaim_index = next(
                (
                    candidate
                    for candidate in range(index, min(last_index, index + config.reclaim_within_bars) + 1)
                    if klines[candidate].close_price > swept_level
                ),
                None,
            )
            if reclaim_index is None:
                continue
            age_bars = last_index - reclaim_index
            return LiquiditySweepState(
                side="long",
                swept_level=swept_level,
                sweep_at=klines[index].end_at,
                reclaim_at=klines[reclaim_index].end_at,
                age_bars=age_bars,
                is_active=age_bars <= self._smc_config.entry.max_setup_age_bars,
            )
        return None

    def _find_short_sweep(self, klines: list[KlineEvent]) -> LiquiditySweepState | None:
        config = self._smc_config.sweep
        last_index = len(klines) - 1
        for index in range(last_index, config.lookback_bars - 1, -1):
            window = klines[index - config.lookback_bars : index]
            if not window:
                continue
            swept_level = max(item.high_price for item in window)
            if klines[index].high_price < swept_level * _threshold_ratio(config.min_penetration_bps):
                continue
            reclaim_index = next(
                (
                    candidate
                    for candidate in range(index, min(last_index, index + config.reclaim_within_bars) + 1)
                    if klines[candidate].close_price < swept_level
                ),
                None,
            )
            if reclaim_index is None:
                continue
            age_bars = last_index - reclaim_index
            return LiquiditySweepState(
                side="short",
                swept_level=swept_level,
                sweep_at=klines[index].end_at,
                reclaim_at=klines[reclaim_index].end_at,
                age_bars=age_bars,
                is_active=age_bars <= self._smc_config.entry.max_setup_age_bars,
            )
        return None

    def _compute_fvgs(self, klines: list[KlineEvent]) -> list[FairValueGapZone]:
        if not self._has_smc_config or len(klines) < 3:
            return []
        config = self._smc_config.fvg
        latest = klines[-1]
        zones: list[FairValueGapZone] = []
        for index in range(2, len(klines)):
            a, _, c = klines[index - 2], klines[index - 1], klines[index]
            if c.low_price > a.high_price and _bps_change(c.low_price, a.high_price) >= Decimal(str(config.min_gap_bps)):
                lower, upper = a.high_price, c.low_price
                invalidated = any(item.close_price < lower for item in klines[index + 1 :])
                age_bars = len(klines) - 1 - index
                if not invalidated and age_bars <= config.max_age_bars:
                    zones.append(
                        FairValueGapZone(
                            side="long",
                            lower_bound=lower,
                            upper_bound=upper,
                            created_at=c.end_at,
                            age_bars=age_bars,
                            touched=_touches_zone(latest, lower=lower, upper=upper),
                        )
                    )
            if c.high_price < a.low_price and _bps_change(a.low_price, c.high_price) >= Decimal(str(config.min_gap_bps)):
                lower, upper = c.high_price, a.low_price
                invalidated = any(item.close_price > upper for item in klines[index + 1 :])
                age_bars = len(klines) - 1 - index
                if not invalidated and age_bars <= config.max_age_bars:
                    zones.append(
                        FairValueGapZone(
                            side="short",
                            lower_bound=lower,
                            upper_bound=upper,
                            created_at=c.end_at,
                            age_bars=age_bars,
                            touched=_touches_zone(latest, lower=lower, upper=upper),
                        )
                    )
        return sorted(zones, key=lambda item: item.created_at, reverse=True)

    def _compute_order_blocks(
        self,
        klines: list[KlineEvent],
        events: list[_StructureEventInfo],
    ) -> list[OrderBlockZone]:
        if not self._has_smc_config or not klines or not events:
            return []
        config = self._smc_config.order_block
        zones: list[OrderBlockZone] = []
        for event in reversed(events):
            if event.event_type in {"bos_up", "choch_up"}:
                candidate = next(
                    (klines[index] for index in range(event.index - 1, -1, -1) if klines[index].close_price < klines[index].open_price),
                    None,
                )
                if candidate is None:
                    continue
                displacement = _bps_change(event.break_price, candidate.close_price)
                lower, upper = candidate.low_price, candidate.open_price
                invalidated = any(item.close_price < lower for item in klines[event.index + 1 :])
                age_bars = len(klines) - 1 - event.index
                if displacement >= Decimal(str(config.impulse_displacement_bps)) and age_bars <= config.max_age_bars and not invalidated:
                    zones.append(
                        OrderBlockZone(
                            side="long",
                            lower_bound=lower,
                            upper_bound=upper,
                            created_at=klines[event.index].end_at,
                            age_bars=age_bars,
                            source_event_type=event.event_type,
                            touched=_touches_zone(klines[-1], lower=lower, upper=upper),
                        )
                    )
            if event.event_type in {"bos_down", "choch_down"}:
                candidate = next(
                    (klines[index] for index in range(event.index - 1, -1, -1) if klines[index].close_price > klines[index].open_price),
                    None,
                )
                if candidate is None:
                    continue
                displacement = _bps_change(candidate.close_price, event.break_price)
                lower, upper = candidate.open_price, candidate.high_price
                invalidated = any(item.close_price > upper for item in klines[event.index + 1 :])
                age_bars = len(klines) - 1 - event.index
                if displacement >= Decimal(str(config.impulse_displacement_bps)) and age_bars <= config.max_age_bars and not invalidated:
                    zones.append(
                        OrderBlockZone(
                            side="short",
                            lower_bound=lower,
                            upper_bound=upper,
                            created_at=klines[event.index].end_at,
                            age_bars=age_bars,
                            source_event_type=event.event_type,
                            touched=_touches_zone(klines[-1], lower=lower, upper=upper),
                        )
                    )
        return sorted(zones, key=lambda item: item.created_at, reverse=True)

    def _compute_orderbook_state(
        self,
        snapshot: MarketSnapshot,
        orderbooks: deque[OrderBookEvent],
    ) -> OrderBookFeatureState:
        if not self._has_smc_config:
            imbalance_levels = 5
            min_abs_imbalance = 0.10
            wall_distance_bps = 20.0
            wall_size_vs_median = 3.0
            min_persistence = 3
        else:
            config = self._smc_config.orderbook
            imbalance_levels = config.imbalance_levels
            min_abs_imbalance = config.min_abs_imbalance
            wall_distance_bps = config.wall_distance_bps
            wall_size_vs_median = config.wall_size_vs_median
            min_persistence = config.wall_min_persistence_snapshots

        top_bid = Decimal("0")
        top_ask = Decimal("0")
        bid_wall_price: Decimal | None = None
        ask_wall_price: Decimal | None = None
        bid_wall_size: Decimal | None = None
        ask_wall_size: Decimal | None = None
        wall_persistence = 0

        if snapshot.orderbook is not None:
            top_bid = sum((item.size for item in snapshot.orderbook.bids[:imbalance_levels]), start=Decimal("0"))
            top_ask = sum((item.size for item in snapshot.orderbook.asks[:imbalance_levels]), start=Decimal("0"))
            mid = _mid_price(snapshot)
            if mid is not None:
                bid_wall_price, bid_wall_size, wall_persistence = self._detect_wall(
                    levels=snapshot.orderbook.bids[:10],
                    history=orderbooks,
                    side="bid",
                    mid=mid,
                    wall_distance_bps=wall_distance_bps,
                    wall_size_vs_median=wall_size_vs_median,
                    min_persistence=min_persistence,
                )
                ask_wall_price, ask_wall_size, ask_persistence = self._detect_wall(
                    levels=snapshot.orderbook.asks[:10],
                    history=orderbooks,
                    side="ask",
                    mid=mid,
                    wall_distance_bps=wall_distance_bps,
                    wall_size_vs_median=wall_size_vs_median,
                    min_persistence=min_persistence,
                )
                wall_persistence = max(wall_persistence, ask_persistence)

        denominator = top_bid + top_ask
        imbalance = float((top_bid - top_ask) / denominator) if denominator != 0 else 0.0
        return OrderBookFeatureState(
            imbalance_levels=imbalance_levels,
            imbalance=imbalance,
            has_fresh_orderbook=not snapshot.data_is_stale and snapshot.orderbook is not None,
            supportive_long_imbalance=imbalance >= min_abs_imbalance,
            supportive_short_imbalance=imbalance <= -min_abs_imbalance,
            has_bid_wall=bid_wall_price is not None,
            has_ask_wall=ask_wall_price is not None,
            bid_wall_price=bid_wall_price,
            ask_wall_price=ask_wall_price,
            bid_wall_size=bid_wall_size,
            ask_wall_size=ask_wall_size,
            wall_persistence=wall_persistence,
        )

    def _detect_wall(
        self,
        *,
        levels,
        history: deque[OrderBookEvent],
        side: str,
        mid: Decimal,
        wall_distance_bps: float,
        wall_size_vs_median: float,
        min_persistence: int,
    ) -> tuple[Decimal | None, Decimal | None, int]:
        historical_sizes: list[Decimal] = []
        for snapshot in history:
            top_levels = snapshot.bids[:10] if side == "bid" else snapshot.asks[:10]
            historical_sizes.extend(level.size for level in top_levels)
        median_size = median(historical_sizes) if historical_sizes else Decimal("0")
        if median_size == 0:
            return None, None, 0

        for level in levels:
            distance_bps = abs(_bps_change(level.price, mid))
            if distance_bps > Decimal(str(wall_distance_bps)):
                continue
            if level.size < median_size * Decimal(str(wall_size_vs_median)):
                continue
            persistence = self._wall_persistence(history=history, price=level.price, side=side)
            if persistence >= min_persistence:
                return level.price, level.size, persistence
        return None, None, 0

    def _wall_persistence(self, *, history: deque[OrderBookEvent], price: Decimal, side: str) -> int:
        count = 0
        for snapshot in reversed(history):
            levels = snapshot.bids[:10] if side == "bid" else snapshot.asks[:10]
            if any(level.price == price for level in levels):
                count += 1
            else:
                break
        return count

    def _compute_open_interest_state(self, history: deque[OpenInterestEvent]) -> OpenInterestFeatureState:
        if not self._has_smc_config or len(history) <= self._smc_config.open_interest.lookback_points:
            return OpenInterestFeatureState(available=False, lookback_points=len(history))
        latest = history[-1].open_interest
        reference = history[-(self._smc_config.open_interest.lookback_points + 1)].open_interest
        delta_bps = _bps_change(latest, reference)
        threshold = Decimal(str(self._smc_config.open_interest.min_delta_bps))
        return OpenInterestFeatureState(
            available=True,
            delta_bps=delta_bps,
            supportive_long=delta_bps >= threshold,
            supportive_short=delta_bps <= -threshold,
            lookback_points=self._smc_config.open_interest.lookback_points,
        )

    def _compute_funding_state(
        self,
        snapshot: MarketSnapshot,
        latest_funding: FundingRateEvent | None,
    ) -> FundingFeatureState:
        funding = snapshot.funding_rate or latest_funding
        if not self._has_smc_config:
            rate = funding.funding_rate if funding is not None else Decimal("0")
            return FundingFeatureState(enabled=True, available=funding is not None, funding_rate=rate)

        config = self._smc_config.funding
        rate = funding.funding_rate if funding is not None else Decimal("0")
        available = funding is not None
        if not available and config.missing_is_neutral:
            return FundingFeatureState(enabled=config.enabled, available=False, funding_rate=Decimal("0"))
        threshold = Decimal(str(config.adverse_threshold))
        return FundingFeatureState(
            enabled=config.enabled,
            available=available,
            funding_rate=rate,
            blocks_long=config.enabled and rate > threshold,
            blocks_short=config.enabled and rate < -threshold,
        )

    def _compute_liquidation_state(
        self,
        snapshot: MarketSnapshot,
        history: deque[LiquidationEvent],
    ) -> LiquidationFeatureState:
        if not self._has_smc_config:
            return LiquidationFeatureState(enabled=False, available=bool(history))
        config = self._smc_config.liquidations
        if not config.enabled:
            return LiquidationFeatureState(enabled=False, available=bool(history))
        window_start = snapshot.as_of - timedelta(seconds=config.burst_window_seconds)
        in_window = [event for event in history if event.event_ts >= window_start]
        long_count = sum(1 for event in in_window if event.side.lower() == "sell")
        short_count = sum(1 for event in in_window if event.side.lower() == "buy")
        return LiquidationFeatureState(
            enabled=True,
            available=bool(history),
            supportive_long=long_count >= config.min_same_side_events,
            supportive_short=short_count >= config.min_same_side_events,
            same_side_events=max(long_count, short_count),
            window_seconds=config.burst_window_seconds,
        )

    def _build_setup_candidates(
        self,
        fvgs: list[FairValueGapZone],
        order_blocks: list[OrderBlockZone],
    ) -> list[SetupCandidate]:
        candidates: list[SetupCandidate] = []
        for zone in fvgs:
            candidates.append(
                SetupCandidate(
                    side=zone.side,
                    zone_type="fvg",
                    lower_bound=zone.lower_bound,
                    upper_bound=zone.upper_bound,
                    created_at=zone.created_at,
                    age_bars=zone.age_bars,
                    touched=zone.touched,
                )
            )
        for zone in order_blocks:
            candidates.append(
                SetupCandidate(
                    side=zone.side,
                    zone_type="order_block",
                    lower_bound=zone.lower_bound,
                    upper_bound=zone.upper_bound,
                    created_at=zone.created_at,
                    age_bars=zone.age_bars,
                    touched=zone.touched,
                    metadata={"source_event_type": zone.source_event_type},
                )
            )
        return sorted(candidates, key=lambda item: item.created_at, reverse=True)
