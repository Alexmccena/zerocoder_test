from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal

from trading_bot.config.schema import AppSettings
from trading_bot.domain.enums import RiskDecisionType, TradeAction
from trading_bot.domain.models import ExecutionPlan, OrderIntent, RiskDecision, RuntimeState, TradeIntent, utc_now


def _is_open_action(action: TradeAction) -> bool:
    return action in {TradeAction.OPEN_LONG, TradeAction.OPEN_SHORT}


def _is_close_action(action: TradeAction) -> bool:
    return action in {TradeAction.CLOSE_LONG, TradeAction.CLOSE_SHORT}


def _utc_date_key(value: datetime) -> str:
    return value.astimezone(timezone.utc).date().isoformat()


def _next_utc_day(value: datetime) -> datetime:
    utc_value = value.astimezone(timezone.utc)
    tomorrow = utc_value.date() + timedelta(days=1)
    return datetime.combine(tomorrow, datetime.min.time(), tzinfo=timezone.utc)


def _floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    units = (value / step).to_integral_value(rounding=ROUND_DOWN)
    return units * step


class BasicRiskEngine:
    def __init__(self, *, config: AppSettings) -> None:
        self.config = config

    async def assess(self, intent: TradeIntent, state: RuntimeState, snapshot) -> RiskDecision:
        if snapshot.instrument is None or snapshot.orderbook is None:
            return self._decision(
                RiskDecisionType.HALT,
                ["missing_market_state"],
                payload={"symbol": intent.symbol},
            )
        if state.account_state is None:
            return self._decision(RiskDecisionType.HALT, ["missing_account_state"])
        if snapshot.data_is_stale:
            return self._decision(RiskDecisionType.REJECT, ["stale_market_data"])

        if _is_open_action(intent.action):
            return self._assess_open_intent(intent=intent, state=state, snapshot=snapshot)
        if _is_close_action(intent.action):
            return self._assess_close_intent(intent=intent, state=state)
        return self._decision(RiskDecisionType.HALT, ["unsupported_trade_action"])

    def _assess_open_intent(self, *, intent: TradeIntent, state: RuntimeState, snapshot) -> RiskDecision:
        kill_switch_reason = self._active_open_kill_switch(state=state, intent=intent, snapshot=snapshot)
        if kill_switch_reason is not None:
            return self._decision(
                RiskDecisionType.REJECT,
                [kill_switch_reason],
                payload={"kill_switch_reason": kill_switch_reason},
            )

        has_open_entry_order = any(
            order.symbol == intent.symbol and not order.reduce_only for order in state.open_orders.values()
        )
        if has_open_entry_order:
            return self._decision(RiskDecisionType.REJECT, ["duplicate_open_order"])

        open_position = state.open_positions.get(intent.symbol)
        if self.config.risk.one_position_per_symbol and open_position is not None:
            return self._decision(RiskDecisionType.REJECT, ["position_already_open"])
        if len(state.open_positions) >= self.config.risk.max_open_positions:
            return self._decision(RiskDecisionType.REJECT, ["max_open_positions"])

        if intent.stop_loss_price is None or intent.take_profit_price is None:
            return self._decision(RiskDecisionType.REJECT, ["missing_protective_levels"])

        geometry_error = self._validate_protective_geometry(intent=intent)
        if geometry_error is not None:
            return self._decision(RiskDecisionType.REJECT, [geometry_error])

        sizing = self._size_open_quantity(intent=intent, state=state, snapshot=snapshot)
        if sizing["reason"] is not None:
            payload = {key: value for key, value in sizing.items() if key != "reason" and value is not None}
            return self._decision(
                RiskDecisionType.REJECT,
                [str(sizing["reason"])],
                payload=payload,
            )

        sized_quantity = sizing["sized_quantity"]
        if not isinstance(sized_quantity, Decimal):
            return self._decision(RiskDecisionType.HALT, ["risk_sizing_failed"])

        entry_order = OrderIntent(
            intent_id=intent.intent_id,
            exchange_name=self.config.exchange.primary,
            execution_venue=state.execution_venue,
            symbol=intent.symbol,
            side=intent.side,
            order_type=intent.entry_type.value,
            quantity=sized_quantity,
            price=intent.limit_price if intent.entry_type.value == "limit" else None,
            ttl_ms=intent.ttl_ms if intent.entry_type.value == "limit" else None,
            submitted_at=intent.generated_at,
            metadata={
                "strategy_name": intent.strategy_name,
                "action": intent.action.value,
                "order_role": "entry",
                **intent.metadata,
            },
        )
        protective_orders = self._build_protective_orders(intent=intent, quantity=sized_quantity, execution_venue=state.execution_venue)
        plan = ExecutionPlan(
            execution_venue=state.execution_venue,
            entry_order=entry_order,
            protective_orders=protective_orders,
            intent_id=intent.intent_id,
            metadata={
                "assessed_at": utc_now().isoformat(),
                "action": intent.action.value,
                "risk_budget": sizing["risk_budget"],
                "stop_distance": sizing["stop_distance"],
                "sized_quantity": str(sized_quantity),
            },
        )
        return self._decision(
            RiskDecisionType.ALLOW,
            [],
            execution_plan=plan,
            payload={
                "risk_budget": sizing["risk_budget"],
                "stop_distance": sizing["stop_distance"],
                "sized_quantity": str(sized_quantity),
            },
        )

    def _assess_close_intent(self, *, intent: TradeIntent, state: RuntimeState) -> RiskDecision:
        open_position = state.open_positions.get(intent.symbol)
        if open_position is None:
            return self._decision(RiskDecisionType.REJECT, ["no_position_to_close"])

        entry_order = OrderIntent(
            intent_id=intent.intent_id,
            exchange_name=self.config.exchange.primary,
            execution_venue=state.execution_venue,
            symbol=intent.symbol,
            side=intent.side,
            order_type=intent.entry_type.value,
            quantity=open_position.quantity,
            price=intent.limit_price if intent.entry_type.value == "limit" else None,
            ttl_ms=intent.ttl_ms if intent.entry_type.value == "limit" else None,
            reduce_only=True,
            submitted_at=intent.generated_at,
            metadata={
                "strategy_name": intent.strategy_name,
                "action": intent.action.value,
                **intent.metadata,
            },
        )
        plan = ExecutionPlan(
            execution_venue=state.execution_venue,
            entry_order=entry_order,
            intent_id=intent.intent_id,
            metadata={
                "assessed_at": utc_now().isoformat(),
                "action": intent.action.value,
                "cancel_active_bracket": True,
                "sized_quantity": str(open_position.quantity),
            },
        )
        return self._decision(
            RiskDecisionType.ALLOW,
            [],
            execution_plan=plan,
            payload={
                "risk_budget": "0",
                "stop_distance": "0",
                "sized_quantity": str(open_position.quantity),
            },
        )

    def _active_open_kill_switch(self, *, state: RuntimeState, intent: TradeIntent, snapshot) -> str | None:
        now = intent.generated_at
        kill_switch_state = state.kill_switch_state
        loss_state = state.loss_streak_state

        if kill_switch_state.daily_loss_breached_until is not None and now >= kill_switch_state.daily_loss_breached_until:
            kill_switch_state.daily_loss_breached_until = None
            kill_switch_state.last_reason = None
        if loss_state.cooldown_until is not None and now >= loss_state.cooldown_until:
            loss_state.cooldown_until = None
            kill_switch_state.consecutive_loss_cooldown_until = None

        if kill_switch_state.protection_failure_active:
            kill_switch_state.last_reason = "protection_failure_active"
            return "protection_failure_active"

        day_key = _utc_date_key(now)
        current_equity = state.account_state.equity
        if day_key not in state.day_start_equity_by_utc_date:
            state.day_start_equity_by_utc_date[day_key] = current_equity
        baseline = state.day_start_equity_by_utc_date[day_key]
        if baseline > 0:
            drawdown_ratio = (baseline - current_equity) / baseline
            if drawdown_ratio >= Decimal(str(self.config.risk.max_daily_loss)):
                kill_switch_state.daily_loss_breached_until = _next_utc_day(now)
                kill_switch_state.last_reason = "daily_loss_breached"
                return "daily_loss_breached"
        if kill_switch_state.daily_loss_breached_until is not None and now < kill_switch_state.daily_loss_breached_until:
            kill_switch_state.last_reason = "daily_loss_breached"
            return "daily_loss_breached"

        if loss_state.cooldown_until is not None and now < loss_state.cooldown_until:
            kill_switch_state.consecutive_loss_cooldown_until = loss_state.cooldown_until
            kill_switch_state.last_reason = "consecutive_loss_cooldown_active"
            return "consecutive_loss_cooldown_active"

        funding = snapshot.funding_rate
        if funding is not None and funding.next_funding_at is not None:
            before = timedelta(minutes=self.config.risk.funding_blackout_minutes_before)
            after = timedelta(minutes=self.config.risk.funding_blackout_minutes_after)
            window_start = funding.next_funding_at - before
            window_end = funding.next_funding_at + after
            if window_start <= now <= window_end:
                kill_switch_state.last_reason = "funding_blackout_active"
                return "funding_blackout_active"

        return None

    def _validate_protective_geometry(self, *, intent: TradeIntent) -> str | None:
        if intent.stop_loss_price is None or intent.take_profit_price is None:
            return "missing_protective_levels"
        entry_price = self._entry_reference_price(intent)
        if intent.action == TradeAction.OPEN_LONG and not (intent.stop_loss_price < entry_price < intent.take_profit_price):
            return "invalid_protective_geometry"
        if intent.action == TradeAction.OPEN_SHORT and not (intent.take_profit_price < entry_price < intent.stop_loss_price):
            return "invalid_protective_geometry"
        return None

    def _size_open_quantity(self, *, intent: TradeIntent, state: RuntimeState, snapshot) -> dict[str, str | Decimal | None]:
        account = state.account_state
        instrument = snapshot.instrument
        if account is None or instrument is None:
            return {"reason": "missing_account_or_instrument_state", "risk_budget": None, "stop_distance": None, "sized_quantity": None}

        entry_reference_price = self._entry_reference_price(intent)
        if entry_reference_price <= 0:
            return {"reason": "invalid_reference_price", "risk_budget": None, "stop_distance": None, "sized_quantity": None}

        stop_distance = abs(entry_reference_price - (intent.stop_loss_price or Decimal("0")))
        if stop_distance <= 0:
            return {
                "reason": "invalid_stop_distance",
                "risk_budget": "0",
                "stop_distance": str(stop_distance),
                "sized_quantity": None,
            }

        risk_budget = account.equity * Decimal(str(self.config.risk.risk_per_trade))
        raw_quantity = risk_budget / stop_distance
        max_notional_quantity = (account.available_balance * self.config.risk.leverage_cap) / entry_reference_price
        capped_quantity = min(
            raw_quantity,
            max_notional_quantity,
            instrument.max_order_quantity or raw_quantity,
        )
        sized_quantity = _floor_to_step(capped_quantity, instrument.lot_size)

        if max_notional_quantity <= 0 or account.available_balance <= 0:
            return {
                "reason": "insufficient_balance",
                "risk_budget": str(risk_budget),
                "stop_distance": str(stop_distance),
                "sized_quantity": None,
            }
        if sized_quantity < instrument.min_quantity:
            return {
                "reason": "quantity_below_minimum",
                "risk_budget": str(risk_budget),
                "stop_distance": str(stop_distance),
                "sized_quantity": str(sized_quantity),
            }
        if instrument.min_notional is not None and (sized_quantity * entry_reference_price) < instrument.min_notional:
            return {
                "reason": "quantity_below_min_notional",
                "risk_budget": str(risk_budget),
                "stop_distance": str(stop_distance),
                "sized_quantity": str(sized_quantity),
            }

        return {
            "reason": None,
            "risk_budget": str(risk_budget),
            "stop_distance": str(stop_distance),
            "sized_quantity": sized_quantity,
        }

    def _build_protective_orders(
        self,
        *,
        intent: TradeIntent,
        quantity: Decimal,
        execution_venue,
    ) -> list[OrderIntent]:
        if intent.stop_loss_price is None or intent.take_profit_price is None:
            return []
        close_side = "sell" if intent.side == "buy" else "buy"
        base_metadata = {
            "strategy_name": intent.strategy_name,
            "parent_intent_id": intent.intent_id,
            "setup_side": intent.metadata.get("setup_side"),
        }
        stop_order = OrderIntent(
            intent_id=intent.intent_id,
            exchange_name=self.config.exchange.primary,
            execution_venue=execution_venue,
            symbol=intent.symbol,
            side=close_side,
            order_type="stop_market",
            quantity=quantity,
            stop_price=intent.stop_loss_price,
            reduce_only=True,
            ttl_ms=None,
            submitted_at=intent.generated_at,
            metadata={**base_metadata, "order_role": "stop_loss"},
        )
        take_profit_order = OrderIntent(
            intent_id=intent.intent_id,
            exchange_name=self.config.exchange.primary,
            execution_venue=execution_venue,
            symbol=intent.symbol,
            side=close_side,
            order_type="limit",
            quantity=quantity,
            price=intent.take_profit_price,
            reduce_only=True,
            ttl_ms=None,
            submitted_at=intent.generated_at,
            metadata={**base_metadata, "order_role": "take_profit"},
        )
        return [stop_order, take_profit_order]

    def _entry_reference_price(self, intent: TradeIntent) -> Decimal:
        if intent.entry_type.value == "limit" and intent.limit_price is not None:
            return intent.limit_price
        return intent.reference_price

    def _decision(
        self,
        decision: RiskDecisionType,
        reasons: list[str],
        *,
        execution_plan: ExecutionPlan | None = None,
        payload: dict[str, str | None] | None = None,
    ) -> RiskDecision:
        return RiskDecision(
            decision=decision,
            reasons=reasons,
            execution_plan=execution_plan,
            payload=payload or {},
        )
