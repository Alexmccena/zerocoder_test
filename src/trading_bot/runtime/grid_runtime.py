from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from typing import Any

from trading_bot.config.schema import AppSettings, GridPairConfig
from trading_bot.domain.enums import MarketType
from trading_bot.domain.models import ExecutionPlan, ExecutionResult, MarketSnapshot, OrderIntent
from trading_bot.observability.metrics import AppMetrics
from trading_bot.storage.repositories import (
    GridEventRepository,
    GridOrderLinkRepository,
    GridPairProfileRepository,
    GridPairSnapshotRepository,
)
from trading_bot.strategies.grid_dca_v1 import GridDcaV1Strategy


def _merge_results(base: ExecutionResult, other: ExecutionResult) -> ExecutionResult:
    base.accepted = base.accepted and other.accepted
    base.orders.extend(other.orders)
    base.fills.extend(other.fills)
    if other.position is not None:
        base.position = other.position
    base.positions.extend(other.positions)
    if other.account_state is not None:
        base.account_state = other.account_state
    if other.pnl_snapshot is not None:
        base.pnl_snapshot = other.pnl_snapshot
    if other.reason is not None:
        base.reason = other.reason
    if other.payload:
        base.payload.update(other.payload)
    return base


def _floor_to_step(value: Decimal, step: Decimal | None) -> Decimal:
    if step is None or step <= 0:
        return value
    units = (value / step).to_integral_value(rounding=ROUND_DOWN)
    return units * step


@dataclass(slots=True)
class GridOrderLink:
    symbol: str
    role: str
    stack_index: int | None
    level_index: int | None
    price: Decimal | None
    parent_order_id: str | None = None


@dataclass(slots=True)
class GridPairState:
    profile: GridPairConfig
    enabled: bool
    paused: bool = False
    max_stacks: int = 1
    active_stacks: int = 0
    current_stack_anchor: Decimal | None = None
    stack_anchors: list[Decimal] = field(default_factory=list)
    last_realized_sell_price: Decimal | None = None
    last_price: Decimal | None = None
    pending_buy_levels: list[tuple[int, int, Decimal, Decimal]] = field(default_factory=list)
    buy_orders: dict[str, Decimal] = field(default_factory=dict)
    stop_requested: bool = False


class GridRuntime:
    def __init__(
        self,
        *,
        config: AppSettings,
        strategy: GridDcaV1Strategy,
        state_store,
        execution_engine,
        metrics: AppMetrics,
        profiles_repo: GridPairProfileRepository | None = None,
        snapshots_repo: GridPairSnapshotRepository | None = None,
        links_repo: GridOrderLinkRepository | None = None,
        events_repo: GridEventRepository | None = None,
    ) -> None:
        self.config = config
        self.strategy = strategy
        self.state_store = state_store
        self.execution_engine = execution_engine
        self.metrics = metrics
        self.profiles_repo = profiles_repo
        self.snapshots_repo = snapshots_repo
        self.links_repo = links_repo
        self.events_repo = events_repo
        self.run_session_id: str | None = None
        self._states: dict[str, GridPairState] = {}
        self._order_links: dict[str, GridOrderLink] = {}

    @property
    def is_enabled(self) -> bool:
        return self.config.strategy.name == "grid_dca_v1"

    def set_run_session(self, run_session_id: str) -> None:
        self.run_session_id = run_session_id

    async def initialize(self, *, as_of: datetime) -> None:
        for pair in self.config.strategy.grid_dca_v1.pairs:
            max_stacks = max(int(pair.budget_quote / pair.stack_size_quote), 1)
            self._states[pair.symbol] = GridPairState(
                profile=pair,
                enabled=pair.enabled,
                paused=False,
                max_stacks=max_stacks,
            )
            await self._upsert_profile(pair=pair, paused=False, enabled=pair.enabled)

        if self.snapshots_repo is not None and self.run_session_id is not None:
            for record in await self.snapshots_repo.list_snapshots(run_session_id=self.run_session_id):
                state = self._states.get(record.symbol)
                if state is None:
                    continue
                state.enabled = bool(record.active)
                state.paused = bool(record.paused)
                state.max_stacks = max(int(record.max_stacks), 1)
                state.active_stacks = max(int(record.active_stacks), 0)
                state.current_stack_anchor = record.current_stack_anchor
                state.last_realized_sell_price = record.last_realized_sell_price
                state.last_price = record.last_price
                state.stack_anchors = [
                    Decimal(str(item))
                    for item in list((record.state_json or {}).get("stack_anchors", []))
                    if item is not None
                ]
                state.pending_buy_levels = [
                    (
                        int(item["stack_index"]),
                        int(item["level_index"]),
                        Decimal(str(item["price"])),
                        Decimal(str(item["quantity"])),
                    )
                    for item in list((record.state_json or {}).get("pending_buy_levels", []))
                    if isinstance(item, dict)
                ]

        # Recover grid links from open orders already present in runtime state.
        for order in self.state_store.state.open_orders.values():
            metadata = dict(order.raw_payload.get("metadata", {}) if isinstance(order.raw_payload, dict) else {})
            role = metadata.get("grid_role")
            if role not in {"buy", "sell"}:
                continue
            stack_index = metadata.get("grid_stack_index")
            level_index = metadata.get("grid_level_index")
            parent_order_id = metadata.get("grid_parent_order_id")
            self._order_links[order.order_id] = GridOrderLink(
                symbol=order.symbol,
                role=role,
                stack_index=int(stack_index) if stack_index is not None else None,
                level_index=int(level_index) if level_index is not None else None,
                price=order.price,
                parent_order_id=str(parent_order_id) if parent_order_id is not None else None,
            )
            if role == "buy":
                state = self._states.get(order.symbol)
                if state is not None and order.price is not None:
                    state.buy_orders[order.order_id] = order.price

        await self._persist_all_snapshots(as_of=as_of)

    def list_pairs(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for symbol, state in sorted(self._states.items(), key=lambda item: item[0]):
            budget_margin_quote = state.profile.budget_margin_quote
            stack_margin_quote = state.profile.stack_margin_quote
            rows.append(
                {
                    "symbol": symbol,
                    "enabled": state.enabled,
                    "paused": state.paused,
                    "leverage": str(state.profile.leverage),
                    "max_stacks": state.max_stacks,
                    "active_stacks": state.active_stacks,
                    "budget_quote": str(state.profile.budget_quote),
                    "budget_margin_quote": str(budget_margin_quote),
                    "stack_size_quote": str(state.profile.stack_size_quote),
                    "stack_margin_quote": str(stack_margin_quote),
                    "current_stack_anchor": str(state.current_stack_anchor) if state.current_stack_anchor is not None else None,
                    "last_realized_sell_price": (
                        str(state.last_realized_sell_price) if state.last_realized_sell_price is not None else None
                    ),
                    "last_price": str(state.last_price) if state.last_price is not None else None,
                    "pending_buy_levels": len(state.pending_buy_levels),
                    "working_buy_orders": len(state.buy_orders),
                    "stop_requested": state.stop_requested,
                }
            )
        return rows

    async def add_pair(self, *, symbol: str) -> tuple[bool, str]:
        symbol = symbol.upper()
        if symbol not in set(self.config.symbols.allowlist):
            return False, f"Pair {symbol} is not in symbols.allowlist."
        if symbol in self._states:
            return False, f"Pair {symbol} already exists."

        template = self.strategy.find_pair(symbol)
        if template is None and self.config.strategy.grid_dca_v1.pairs:
            source = self.config.strategy.grid_dca_v1.pairs[0]
            template = source.model_copy(update={"symbol": symbol})
        if template is None:
            return False, "No grid profile template configured in strategy.grid_dca_v1.pairs."

        max_stacks = max(int(template.budget_quote / template.stack_size_quote), 1)
        self._states[symbol] = GridPairState(
            profile=template,
            enabled=True,
            paused=False,
            max_stacks=max_stacks,
        )
        await self._upsert_profile(pair=template, paused=False, enabled=True)
        return True, f"Pair {symbol} added."

    async def pause_pair(self, *, symbol: str) -> tuple[bool, str]:
        symbol = symbol.upper()
        state = self._states.get(symbol)
        if state is None:
            return False, f"Pair {symbol} not found."
        state.paused = True
        await self._upsert_profile(pair=state.profile, paused=True, enabled=state.enabled)
        return True, f"Pair {symbol} paused."

    async def resume_pair(self, *, symbol: str) -> tuple[bool, str]:
        symbol = symbol.upper()
        state = self._states.get(symbol)
        if state is None:
            return False, f"Pair {symbol} not found."
        state.paused = False
        await self._upsert_profile(pair=state.profile, paused=False, enabled=state.enabled)
        return True, f"Pair {symbol} resumed."

    async def stop_pair(self, *, symbol: str) -> tuple[bool, str]:
        symbol = symbol.upper()
        state = self._states.get(symbol)
        if state is None:
            return False, f"Pair {symbol} not found."
        state.enabled = False
        state.paused = True
        state.stop_requested = True
        await self._upsert_profile(pair=state.profile, paused=True, enabled=False)
        return True, f"Pair {symbol} stop requested."

    async def on_market_event(self, *, snapshot: MarketSnapshot, as_of: datetime) -> ExecutionResult:
        state = self._states.get(snapshot.symbol)
        if state is None:
            return ExecutionResult(accepted=True)
        reference_price = self._reference_price(snapshot)
        if reference_price is not None:
            state.last_price = reference_price

        aggregate = ExecutionResult(accepted=True)
        if state.stop_requested:
            cancelled = await self._cancel_symbol_buy_orders(symbol=snapshot.symbol, as_of=as_of)
            _merge_results(aggregate, cancelled)
            state.stop_requested = False
            await self._append_event(
                symbol=snapshot.symbol,
                event_type="pair_stopped",
                payload={"symbol": snapshot.symbol},
            )

        if not state.enabled or state.paused or reference_price is None:
            await self._persist_snapshot(state=state, symbol=snapshot.symbol, as_of=as_of)
            return aggregate

        if state.active_stacks == 0:
            self._activate_stack(state=state, anchor_price=reference_price)
            await self._append_event(
                symbol=snapshot.symbol,
                event_type="stack_activated",
                payload={"stack_index": state.active_stacks, "anchor_price": str(reference_price)},
            )

        if (
            state.current_stack_anchor is not None
            and state.active_stacks < state.max_stacks
            and reference_price <= state.current_stack_anchor * (Decimal("1") - (Decimal(str(state.profile.lower_threshold_pct)) / Decimal("100")))
        ):
            self._activate_stack(state=state, anchor_price=reference_price)
            await self._append_event(
                symbol=snapshot.symbol,
                event_type="stack_activated_lower_threshold",
                payload={"stack_index": state.active_stacks, "anchor_price": str(reference_price)},
            )

        if (
            state.last_realized_sell_price is not None
            and reference_price >= state.last_realized_sell_price * (Decimal("1") + (Decimal(str(state.profile.upper_threshold_pct)) / Decimal("100")))
        ):
            shifted = await self._shift_grid_up(state=state, snapshot=snapshot, as_of=as_of)
            _merge_results(aggregate, shifted)

        placements = await self._place_pending_buys(state=state, snapshot=snapshot, as_of=as_of)
        _merge_results(aggregate, placements)
        await self._persist_snapshot(state=state, symbol=snapshot.symbol, as_of=as_of)
        return aggregate

    async def on_execution_result(self, *, result: ExecutionResult, as_of: datetime) -> ExecutionResult:
        aggregate = ExecutionResult(accepted=True)
        for order in result.orders:
            metadata = dict(order.raw_payload.get("metadata", {}) if isinstance(order.raw_payload, dict) else {})
            role = metadata.get("grid_role")
            if role in {"buy", "sell"}:
                stack_index = metadata.get("grid_stack_index")
                level_index = metadata.get("grid_level_index")
                parent_order_id = metadata.get("grid_parent_order_id")
                self._order_links[order.order_id] = GridOrderLink(
                    symbol=order.symbol,
                    role=role,
                    stack_index=int(stack_index) if stack_index is not None else None,
                    level_index=int(level_index) if level_index is not None else None,
                    price=order.price,
                    parent_order_id=str(parent_order_id) if parent_order_id is not None else None,
                )
                if role == "buy" and order.price is not None and order.status in {"new", "working", "partially_filled"}:
                    state = self._states.get(order.symbol)
                    if state is not None:
                        state.buy_orders[order.order_id] = order.price
                if order.status in {"filled", "rejected", "expired", "cancelled"}:
                    state = self._states.get(order.symbol)
                    if state is not None:
                        state.buy_orders.pop(order.order_id, None)
                await self._upsert_link(order=order, role=role)
                if order.status in {"filled", "rejected", "expired", "cancelled"}:
                    await self._mark_link_terminal(order_id=order.order_id)

        for fill in result.fills:
            link = self._order_links.get(fill.order_id)
            if link is None:
                continue
            state = self._states.get(fill.symbol)
            if state is None:
                continue
            if link.role == "buy":
                sell_price = fill.price * (Decimal("1") + (Decimal(str(state.profile.take_profit_pct)) / Decimal("100")))
                tp_result = await self._place_take_profit_sell(
                    state=state,
                    symbol=fill.symbol,
                    quantity=fill.quantity,
                    price=sell_price,
                    as_of=fill.filled_at,
                    parent_buy_order_id=fill.order_id,
                )
                _merge_results(aggregate, tp_result)
                await self._append_event(
                    symbol=fill.symbol,
                    event_type="buy_filled_tp_created",
                    payload={
                        "buy_order_id": fill.order_id,
                        "fill_price": str(fill.price),
                        "fill_quantity": str(fill.quantity),
                        "tp_price": str(sell_price),
                    },
                )
            elif link.role == "sell":
                state.last_realized_sell_price = fill.price
                await self._append_event(
                    symbol=fill.symbol,
                    event_type="sell_realized",
                    payload={
                        "sell_order_id": fill.order_id,
                        "fill_price": str(fill.price),
                        "fill_quantity": str(fill.quantity),
                    },
                )

        for symbol, state in self._states.items():
            await self._persist_snapshot(state=state, symbol=symbol, as_of=as_of)
        return aggregate

    def status_payload(self) -> dict[str, object]:
        return {"pairs": self.list_pairs()}

    async def _place_pending_buys(self, *, state: GridPairState, snapshot: MarketSnapshot, as_of: datetime) -> ExecutionResult:
        aggregate = ExecutionResult(accepted=True)
        if not state.pending_buy_levels:
            return aggregate
        actions_left = self.config.strategy.grid_dca_v1.max_actions_per_tick
        instrument = snapshot.instrument
        lot_step = getattr(instrument, "lot_size", None)
        tick_step = getattr(instrument, "tick_size", None)
        while state.pending_buy_levels and actions_left > 0:
            stack_index, level_index, raw_price, raw_quantity = state.pending_buy_levels.pop(0)
            price = _floor_to_step(raw_price, tick_step)
            quantity = _floor_to_step(raw_quantity, lot_step)
            if price <= 0 or quantity <= 0:
                continue
            submitted = await self._submit_grid_order(
                symbol=snapshot.symbol,
                side="buy",
                price=price,
                quantity=quantity,
                reduce_only=False,
                as_of=as_of,
                metadata={
                    "grid_role": "buy",
                    "grid_stack_index": stack_index,
                    "grid_level_index": level_index,
                },
            )
            _merge_results(aggregate, submitted)
            actions_left -= 1
        return aggregate

    async def _shift_grid_up(self, *, state: GridPairState, snapshot: MarketSnapshot, as_of: datetime) -> ExecutionResult:
        aggregate = ExecutionResult(accepted=True)
        if not state.buy_orders:
            return aggregate
        lowest_order_id, _ = min(state.buy_orders.items(), key=lambda item: item[1])
        cancelled = await self.execution_engine.cancel_order(lowest_order_id, as_of=as_of)
        _merge_results(aggregate, cancelled)
        state.buy_orders.pop(lowest_order_id, None)
        if state.buy_orders:
            highest_buy = max(state.buy_orders.values())
        else:
            highest_buy = snapshot.last_trade.price if snapshot.last_trade is not None else state.last_price
        if highest_buy is None:
            return aggregate
        step_pct = Decimal(str(state.profile.corridor_pct)) / Decimal(str(state.profile.orders_per_stack)) / Decimal("100")
        new_price = highest_buy * (Decimal("1") + step_pct)
        per_order_quote = state.profile.stack_size_quote / Decimal(state.profile.orders_per_stack)
        new_qty = per_order_quote / new_price if new_price > 0 else Decimal("0")
        submitted = await self._submit_grid_order(
            symbol=snapshot.symbol,
            side="buy",
            price=new_price,
            quantity=new_qty,
            reduce_only=False,
            as_of=as_of,
            metadata={
                "grid_role": "buy",
                "grid_stack_index": state.active_stacks,
                "grid_level_index": state.profile.orders_per_stack + 1,
                "grid_shifted": True,
            },
        )
        _merge_results(aggregate, submitted)
        await self._append_event(
            symbol=snapshot.symbol,
            event_type="upper_threshold_shift",
            payload={"cancelled_order_id": lowest_order_id, "new_price": str(new_price)},
        )
        return aggregate

    async def _place_take_profit_sell(
        self,
        *,
        state: GridPairState,
        symbol: str,
        quantity: Decimal,
        price: Decimal,
        as_of: datetime,
        parent_buy_order_id: str,
    ) -> ExecutionResult:
        reduce_only = self.config.exchange.market_type == MarketType.LINEAR_PERP
        metadata = {
            "grid_role": "sell",
            "grid_parent_order_id": parent_buy_order_id,
        }
        if reduce_only:
            metadata["close_reason"] = "grid_take_profit"
        return await self._submit_grid_order(
            symbol=symbol,
            side="sell",
            price=price,
            quantity=quantity,
            reduce_only=reduce_only,
            as_of=as_of,
            metadata=metadata,
        )

    async def _submit_grid_order(
        self,
        *,
        symbol: str,
        side: str,
        price: Decimal,
        quantity: Decimal,
        reduce_only: bool,
        as_of: datetime,
        metadata: dict[str, Any],
    ) -> ExecutionResult:
        plan = ExecutionPlan(
            execution_venue=self.state_store.state.execution_venue,
            entry_order=OrderIntent(
                exchange_name=self.config.exchange.primary,
                execution_venue=self.state_store.state.execution_venue,
                symbol=symbol,
                side=side,
                order_type="limit",
                quantity=quantity,
                price=price,
                reduce_only=reduce_only,
                ttl_ms=None,
                submitted_at=as_of,
                metadata={
                    "strategy_name": "grid_dca_v1",
                    "action": f"grid_{side}",
                    **metadata,
                },
            ),
            intent_id=f"grid:{symbol}:{metadata.get('grid_role', side)}:{as_of.timestamp()}",
            metadata={"grid_runtime": True, "grid_symbol": symbol},
        )
        submitted = await self.execution_engine.submit(plan)
        for order in submitted.orders:
            role = str(metadata.get("grid_role", "buy"))
            link = GridOrderLink(
                symbol=order.symbol,
                role=role,
                stack_index=int(metadata["grid_stack_index"]) if metadata.get("grid_stack_index") is not None else None,
                level_index=int(metadata["grid_level_index"]) if metadata.get("grid_level_index") is not None else None,
                price=order.price,
                parent_order_id=str(metadata["grid_parent_order_id"]) if metadata.get("grid_parent_order_id") is not None else None,
            )
            self._order_links[order.order_id] = link
            if role == "buy" and order.price is not None:
                state = self._states.get(order.symbol)
                if state is not None:
                    state.buy_orders[order.order_id] = order.price
            await self._upsert_link(order=order, role=role)
        await self._append_event(
            symbol=symbol,
            event_type="order_submitted",
            payload={
                "side": side,
                "price": str(price),
                "quantity": str(quantity),
                "reduce_only": reduce_only,
                "metadata": metadata,
            },
        )
        self.metrics.record_strategy_intent("grid_dca_v1", f"submit_{side}")
        return submitted

    async def _cancel_symbol_buy_orders(self, *, symbol: str, as_of: datetime) -> ExecutionResult:
        aggregate = ExecutionResult(accepted=True)
        state = self._states.get(symbol)
        if state is None:
            return aggregate
        for order_id in list(state.buy_orders):
            cancelled = await self.execution_engine.cancel_order(order_id, as_of=as_of)
            _merge_results(aggregate, cancelled)
            await self._mark_link_terminal(order_id=order_id)
        state.buy_orders.clear()
        return aggregate

    def _activate_stack(self, *, state: GridPairState, anchor_price: Decimal) -> None:
        state.active_stacks += 1
        state.current_stack_anchor = anchor_price
        state.stack_anchors.append(anchor_price)
        levels = self.strategy.build_buy_levels(pair=state.profile, anchor_price=anchor_price)
        for level in levels:
            state.pending_buy_levels.append((state.active_stacks, level.level_index, level.price, level.quantity))

    def _reference_price(self, snapshot: MarketSnapshot) -> Decimal | None:
        if snapshot.ticker is not None:
            if snapshot.ticker.last_price is not None:
                return snapshot.ticker.last_price
            if snapshot.ticker.bid_price is not None and snapshot.ticker.ask_price is not None:
                return (snapshot.ticker.bid_price + snapshot.ticker.ask_price) / Decimal("2")
        if snapshot.last_trade is not None:
            return snapshot.last_trade.price
        if snapshot.orderbook is not None and snapshot.orderbook.bids and snapshot.orderbook.asks:
            return (snapshot.orderbook.bids[0].price + snapshot.orderbook.asks[0].price) / Decimal("2")
        latest = snapshot.closed_klines_by_interval.get(self.config.strategy.default_timeframe)
        if latest is not None:
            return latest.close_price
        return None

    async def _upsert_profile(self, *, pair: GridPairConfig, paused: bool, enabled: bool) -> None:
        if self.profiles_repo is None:
            return
        await self.profiles_repo.upsert_profile(
            run_session_id=self.run_session_id,
            symbol=pair.symbol,
            enabled=enabled,
            paused=paused,
            leverage=pair.leverage,
            budget_quote=pair.budget_quote,
            stack_size_quote=pair.stack_size_quote,
            corridor_pct=Decimal(str(pair.corridor_pct)),
            take_profit_pct=Decimal(str(pair.take_profit_pct)),
            orders_per_stack=pair.orders_per_stack,
            lower_threshold_pct=Decimal(str(pair.lower_threshold_pct)),
            upper_threshold_pct=Decimal(str(pair.upper_threshold_pct)),
            config_json=pair.model_dump(mode="json"),
        )

    async def _persist_snapshot(self, *, state: GridPairState, symbol: str, as_of: datetime) -> None:
        if self.snapshots_repo is None:
            return
        await self.snapshots_repo.upsert_snapshot(
            run_session_id=self.run_session_id,
            symbol=symbol,
            active=state.enabled,
            paused=state.paused,
            max_stacks=state.max_stacks,
            active_stacks=state.active_stacks,
            current_stack_anchor=state.current_stack_anchor,
            last_realized_sell_price=state.last_realized_sell_price,
            last_price=state.last_price,
            state_json={
                "stack_anchors": [str(item) for item in state.stack_anchors],
                "pending_buy_levels": [
                    {
                        "stack_index": stack_index,
                        "level_index": level_index,
                        "price": str(price),
                        "quantity": str(quantity),
                    }
                    for stack_index, level_index, price, quantity in state.pending_buy_levels
                ],
                "stop_requested": state.stop_requested,
                "updated_at": as_of.isoformat(),
            },
        )

    async def _persist_all_snapshots(self, *, as_of: datetime) -> None:
        for symbol, state in self._states.items():
            await self._persist_snapshot(state=state, symbol=symbol, as_of=as_of)

    async def _upsert_link(self, *, order, role: str) -> None:
        if self.links_repo is None:
            return
        link = self._order_links.get(order.order_id)
        await self.links_repo.upsert_link(
            run_session_id=self.run_session_id,
            symbol=order.symbol,
            order_id=order.order_id,
            role=role,
            exchange_order_id=order.exchange_order_id,
            client_order_id=order.client_order_id,
            stack_index=link.stack_index if link is not None else None,
            level_index=link.level_index if link is not None else None,
            parent_order_id=link.parent_order_id if link is not None else None,
            payload_json=order.raw_payload,
            status="active" if order.status in {"new", "working", "partially_filled"} else order.status,
        )

    async def _mark_link_terminal(self, *, order_id: str) -> None:
        if self.links_repo is not None:
            await self.links_repo.mark_status(run_session_id=self.run_session_id, order_id=order_id, status="terminal")

    async def _append_event(self, *, symbol: str, event_type: str, payload: dict[str, object]) -> None:
        if self.events_repo is None or not self.config.strategy.grid_dca_v1.persist_events:
            return
        await self.events_repo.append_event(
            run_session_id=self.run_session_id,
            symbol=symbol,
            event_type=event_type,
            payload_json=payload,
        )
