from __future__ import annotations

from decimal import Decimal

from trading_bot.config.schema import AppSettings
from trading_bot.domain.enums import ExecutionVenueKind, RiskDecisionType, TradeAction
from trading_bot.domain.models import ExecutionPlan, OrderIntent, RiskDecision, RuntimeState, TradeIntent, utc_now


class BasicRiskEngine:
    def __init__(self, *, config: AppSettings) -> None:
        self.config = config

    async def assess(self, intent: TradeIntent, state: RuntimeState, snapshot) -> RiskDecision:
        if snapshot.instrument is None or snapshot.orderbook is None:
            return RiskDecision(
                decision=RiskDecisionType.HALT,
                reasons=["missing_market_state"],
                payload={"symbol": intent.symbol},
            )
        if snapshot.data_is_stale:
            return RiskDecision(decision=RiskDecisionType.REJECT, reasons=["stale_market_data"])
        if state.account_state is None:
            return RiskDecision(decision=RiskDecisionType.HALT, reasons=["missing_account_state"])

        has_open_order = any(order.symbol == intent.symbol for order in state.open_orders.values())
        if has_open_order:
            return RiskDecision(decision=RiskDecisionType.REJECT, reasons=["duplicate_open_order"])

        open_position = state.open_positions.get(intent.symbol)
        if intent.action in {TradeAction.OPEN_LONG, TradeAction.OPEN_SHORT}:
            if self.config.risk.one_position_per_symbol and open_position is not None:
                return RiskDecision(decision=RiskDecisionType.REJECT, reasons=["position_already_open"])
            if len(state.open_positions) >= self.config.risk.max_open_positions:
                return RiskDecision(decision=RiskDecisionType.REJECT, reasons=["max_open_positions"])
        elif open_position is None:
            return RiskDecision(decision=RiskDecisionType.REJECT, reasons=["no_position_to_close"])

        notional = intent.quantity * intent.reference_price
        fee_buffer = notional * Decimal("0.01")
        if state.account_state.available_balance < (notional + fee_buffer):
            return RiskDecision(
                decision=RiskDecisionType.REJECT,
                reasons=["insufficient_balance"],
                payload={"required_notional": str(notional)},
            )

        order_intent = OrderIntent(
            intent_id=intent.intent_id,
            exchange_name=self.config.exchange.primary,
            execution_venue=ExecutionVenueKind.PAPER,
            symbol=intent.symbol,
            side=intent.side,
            order_type=intent.entry_type.value,
            quantity=intent.quantity,
            price=intent.limit_price if intent.entry_type.value == "limit" else None,
            ttl_ms=intent.ttl_ms or self.config.execution.limit_ttl_ms,
            submitted_at=intent.generated_at,
            metadata={
                "strategy_name": intent.strategy_name,
                "action": intent.action.value,
                **intent.metadata,
            },
        )
        plan = ExecutionPlan(
            execution_venue=ExecutionVenueKind.PAPER,
            entry_order=order_intent,
            intent_id=intent.intent_id,
            metadata={"assessed_at": utc_now().isoformat()},
        )
        return RiskDecision(
            decision=RiskDecisionType.ALLOW,
            reasons=[],
            execution_plan=plan,
            payload={"notional": str(notional)},
        )
