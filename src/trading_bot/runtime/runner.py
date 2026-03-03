from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from redis.asyncio import Redis
from structlog.stdlib import BoundLogger

from trading_bot.config.schema import AppSettings
from trading_bot.domain.enums import ExecutionVenueKind, RiskDecisionType, RunMode
from trading_bot.domain.models import ExecutionResult, MarketSnapshot, PnlSnapshot
from trading_bot.execution.engine import ExecutionEngine
from trading_bot.marketdata.events import KlineEvent, MarketEvent
from trading_bot.marketdata.snapshots import FeatureProvider, MarketSnapshotBuilder
from trading_bot.observability.metrics import AppMetrics
from trading_bot.runtime.reporting import build_runtime_summary
from trading_bot.runtime.state import RuntimeStateStore
from trading_bot.storage.redis import (
    publish_runtime_account,
    publish_runtime_open_orders,
    publish_runtime_positions,
    publish_runtime_snapshot,
    publish_runtime_status,
)
from trading_bot.storage.repositories import (
    AccountSnapshotRepository,
    ConfigSnapshotRepository,
    FillRepository,
    InstrumentRepository,
    OrderRepository,
    PnlSnapshotRepository,
    PositionRepository,
    RiskDecisionRepository,
    RunSessionRepository,
    SignalEventRepository,
)


class RuntimeRunner:
    def __init__(
        self,
        *,
        config: AppSettings,
        config_hash: str,
        logger: BoundLogger,
        metrics: AppMetrics,
        redis_client: Redis,
        market_feed,
        clock,
        state_store: RuntimeStateStore,
        snapshot_builder: MarketSnapshotBuilder,
        feature_provider: FeatureProvider,
        strategy,
        risk_engine,
        execution_engine: ExecutionEngine,
        run_sessions: RunSessionRepository,
        config_snapshots: ConfigSnapshotRepository,
        instruments: InstrumentRepository,
        signal_events: SignalEventRepository,
        risk_decisions: RiskDecisionRepository,
        orders: OrderRepository,
        fills: FillRepository,
        positions: PositionRepository,
        account_snapshots: AccountSnapshotRepository,
        pnl_snapshots: PnlSnapshotRepository,
        strategy_start_at: datetime | None,
    ) -> None:
        self.config = config
        self.config_hash = config_hash
        self.logger = logger
        self.metrics = metrics
        self.redis_client = redis_client
        self.market_feed = market_feed
        self.clock = clock
        self.state_store = state_store
        self.snapshot_builder = snapshot_builder
        self.feature_provider = feature_provider
        self.strategy = strategy
        self.risk_engine = risk_engine
        self.execution_engine = execution_engine
        self.run_sessions = run_sessions
        self.config_snapshots = config_snapshots
        self.instruments = instruments
        self.signal_events = signal_events
        self.risk_decisions = risk_decisions
        self.orders = orders
        self.fills = fills
        self.positions = positions
        self.account_snapshots = account_snapshots
        self.pnl_snapshots = pnl_snapshots
        self.strategy_start_at = strategy_start_at
        self._redis_degraded = False

    async def run(self, *, duration_seconds: int | None = None, summary_out: Path | None = None) -> dict[str, object]:
        self.metrics.record_runtime_run(self.config.runtime.mode.value)
        run_session = await self.run_sessions.create(
            run_mode=self.config.runtime.mode.value,
            environment=self.config.runtime.environment.value,
            status="running",
            execution_venue=ExecutionVenueKind.PAPER.value,
        )
        state = self.state_store
        state.attach_run_session(run_session.id)

        total_signals = 0
        total_orders = 0
        total_fills = 0
        latest_pnl: PnlSnapshot | None = None
        started_at = time.perf_counter()

        try:
            await self.config_snapshots.create(
                run_session_id=run_session.id,
                config_hash=self.config_hash,
                config_json=self.config.model_dump(mode="json"),
            )
            instruments = await self.market_feed.fetch_instruments(self.config.symbols.allowlist)
            self.snapshot_builder.register_instruments(instruments)
            await self.instruments.upsert_many(instruments)

            initial_account = self.execution_engine.account_state()
            state.set_account(initial_account)
            await self.account_snapshots.create_paper_snapshot(run_session_id=run_session.id, account=initial_account)
            await self._publish_runtime_state(run_session.id, state)

            for event in await self.market_feed.prime(self.config.symbols.allowlist):
                await self._process_market_event(
                    run_session_id=run_session.id,
                    state=state,
                    event=event,
                    evaluate_strategy=False,
                )

            async for event in self.market_feed.stream(self.config.symbols.allowlist):
                if duration_seconds is not None and (time.perf_counter() - started_at) >= duration_seconds:
                    break
                if self.config.runtime.mode in {RunMode.REPLAY, RunMode.BACKTEST}:
                    self.metrics.record_replay_event(event.event_type)
                await self.clock.sleep_until(event.event_ts)
                evaluate_strategy = self.strategy_start_at is None or event.event_ts >= self.strategy_start_at
                signals, order_count, fill_count, persisted_pnl = await self._process_market_event(
                    run_session_id=run_session.id,
                    state=state,
                    event=event,
                    evaluate_strategy=evaluate_strategy,
                )
                total_signals += signals
                total_orders += order_count
                total_fills += fill_count
                if persisted_pnl is not None:
                    latest_pnl = persisted_pnl
        except Exception as exc:
            await self.run_sessions.mark_failed(run_session.id, reason=exc.__class__.__name__)
            self.logger.exception("runtime_run_failed", run_session_id=run_session.id)
            raise
        else:
            summary = build_runtime_summary(
                initial_equity=self.config.paper.initial_equity_usdt,
                account_state=state.state.account_state,
                pnl_snapshot=latest_pnl,
                total_signals=total_signals,
                total_orders=total_orders,
                total_fills=total_fills,
            )
            await self.run_sessions.mark_completed(run_session.id, summary_json=summary)
            if summary_out is not None:
                summary_out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            if self.config.runtime.mode == RunMode.BACKTEST:
                self.metrics.record_backtest_duration(time.perf_counter() - started_at)
                if latest_pnl is not None:
                    self.metrics.set_backtest_max_drawdown(float(latest_pnl.drawdown))
            return summary
        finally:
            await self.market_feed.close()

    async def _process_market_event(
        self,
        *,
        run_session_id: str,
        state: RuntimeStateStore,
        event: MarketEvent,
        evaluate_strategy: bool,
    ) -> tuple[int, int, int, PnlSnapshot | None]:
        self.snapshot_builder.apply_event(event)
        snapshot = self.snapshot_builder.build(event.symbol, as_of=event.event_ts)
        self.feature_provider.observe(event, snapshot)
        state.update_snapshot(snapshot)

        signals = 0
        orders_count = 0
        fills_count = 0
        latest_pnl = await self._persist_execution_result(
            run_session_id=run_session_id,
            state=state,
            result=await self.execution_engine.on_market_event(symbol=event.symbol, snapshot=snapshot, as_of=event.event_ts),
        )
        await self._publish_runtime_state(run_session_id, state)

        if not evaluate_strategy or not self._should_evaluate_strategy(event=event, snapshot=snapshot):
            return signals, orders_count, fills_count, latest_pnl

        features = self.feature_provider.compute(snapshot)
        intents = await self.strategy.evaluate(snapshot, features)
        for intent in intents:
            signals += 1
            self.metrics.record_strategy_intent(intent.strategy_name, intent.action.value)
            signal_event = await self.signal_events.create(
                run_session_id=run_session_id,
                symbol=intent.symbol,
                strategy_name=intent.strategy_name,
                signal_type=intent.action.value,
                payload_json=self._build_signal_payload(
                    intent=intent,
                    features=features,
                ),
            )
            decision = await self.risk_engine.assess(intent, state.state, snapshot)
            self.metrics.record_risk_decision(decision.decision.value)
            await self.risk_decisions.create(
                run_session_id=run_session_id,
                symbol=intent.symbol,
                intent_id=intent.intent_id,
                signal_event_id=signal_event.id,
                decision=decision,
            )
            if decision.decision == RiskDecisionType.HALT:
                raise RuntimeError(f"runtime halted for {intent.symbol}: {','.join(decision.reasons)}")
            if decision.decision != RiskDecisionType.ALLOW or decision.execution_plan is None:
                continue

            submitted = await self.execution_engine.submit(decision.execution_plan)
            orders_count += len(submitted.orders)
            persisted_pnl = await self._persist_execution_result(
                run_session_id=run_session_id,
                state=state,
                result=submitted,
            )
            if persisted_pnl is not None:
                latest_pnl = persisted_pnl

            follow_up = await self.execution_engine.on_market_event(
                symbol=intent.symbol,
                snapshot=snapshot,
                as_of=event.event_ts,
            )
            orders_count += len(follow_up.orders)
            fills_count += len(follow_up.fills)
            persisted_pnl = await self._persist_execution_result(
                run_session_id=run_session_id,
                state=state,
                result=follow_up,
            )
            if persisted_pnl is not None:
                latest_pnl = persisted_pnl

        return signals, orders_count, fills_count, latest_pnl

    def _build_signal_payload(self, *, intent, features) -> dict[str, object]:
        payload: dict[str, object] = {
            "intent": intent.model_dump(mode="json"),
            "features": features.model_dump(mode="json"),
        }
        for key in ("selected_setup", "rule_trace", "setup_context"):
            value = intent.metadata.get(key)
            if value is not None:
                payload[key] = value
        return payload

    async def _persist_execution_result(
        self,
        *,
        run_session_id: str,
        state: RuntimeStateStore,
        result: ExecutionResult,
    ) -> PnlSnapshot | None:
        latest_pnl = result.pnl_snapshot
        for order in result.orders:
            await self.orders.update_lifecycle(run_session_id=run_session_id, order=order)
            state.update_order(order)
        for fill in result.fills:
            fill.run_session_id = run_session_id
            await self.fills.insert_if_new(fill)
        if result.position is not None:
            if result.position.status == "open":
                await self.positions.upsert_snapshot(run_session_id=run_session_id, position=result.position)
            else:
                await self.positions.close_position(run_session_id=run_session_id, position=result.position)
            state.update_position(result.position)
        if result.account_state is not None:
            state.set_account(result.account_state)
            await self.account_snapshots.create_paper_snapshot(run_session_id=run_session_id, account=result.account_state)
        if result.pnl_snapshot is not None:
            result.pnl_snapshot.run_session_id = run_session_id
            await self.pnl_snapshots.append(result.pnl_snapshot)
        return latest_pnl

    async def _publish_runtime_state(self, run_session_id: str, state: RuntimeStateStore) -> None:
        if self._redis_degraded:
            return
        try:
            await publish_runtime_status(
                self.redis_client,
                run_session_id=run_session_id,
                payload={"run_mode": self.config.runtime.mode.value, "config_hash": self.config_hash},
            )
            if state.state.account_state is not None:
                await publish_runtime_account(
                    self.redis_client,
                    run_session_id=run_session_id,
                    payload=state.state.account_state.model_dump(mode="json"),
                )
            await publish_runtime_positions(
                self.redis_client,
                run_session_id=run_session_id,
                payload={"items": [position.model_dump(mode="json") for position in state.state.open_positions.values()]},
            )
            await publish_runtime_open_orders(
                self.redis_client,
                run_session_id=run_session_id,
                payload={"items": [order.model_dump(mode="json") for order in state.state.open_orders.values()]},
            )
            for snapshot in state.state.market_state_by_symbol.values():
                await publish_runtime_snapshot(
                    self.redis_client,
                    run_session_id=run_session_id,
                    symbol=snapshot.symbol,
                    payload=snapshot.model_dump(mode="json"),
                )
        except Exception:
            self._redis_degraded = True
            self.logger.warning("runtime_redis_degraded", run_session_id=run_session_id)

    def _should_evaluate_strategy(self, *, event: MarketEvent, snapshot: MarketSnapshot) -> bool:
        return (
            isinstance(event, KlineEvent)
            and event.is_closed
            and event.interval == self.config.strategy.default_timeframe
            and snapshot.instrument is not None
            and snapshot.orderbook is not None
            and self.config.strategy.default_timeframe in snapshot.closed_klines_by_interval
        )
