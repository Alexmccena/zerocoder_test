from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram, generate_latest


class AppMetrics:
    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry(auto_describe=True)
        self.app_start_total = Counter(
            "tb_app_start_total",
            "Number of application starts.",
            registry=self.registry,
        )
        self.healthcheck_total = Counter(
            "tb_healthcheck_total",
            "Number of health and readiness checks.",
            registry=self.registry,
        )
        self.readiness_fail_total = Counter(
            "tb_readiness_fail_total",
            "Number of failed readiness checks.",
            registry=self.registry,
        )
        self.postgres_ping_seconds = Histogram(
            "tb_postgres_ping_seconds",
            "Latency of PostgreSQL ping operations.",
            registry=self.registry,
        )
        self.redis_ping_seconds = Histogram(
            "tb_redis_ping_seconds",
            "Latency of Redis ping operations.",
            registry=self.registry,
        )
        self.config_validation_fail_total = Counter(
            "tb_config_validation_fail_total",
            "Number of configuration validation failures.",
            registry=self.registry,
        )
        self.capture_run_total = Counter(
            "tb_capture_run_total",
            "Number of capture runs.",
            registry=self.registry,
        )
        self.bybit_rest_requests_total = Counter(
            "tb_bybit_rest_requests_total",
            "Number of Bybit REST requests.",
            ["endpoint", "status"],
            registry=self.registry,
        )
        self.bybit_rest_request_seconds = Histogram(
            "tb_bybit_rest_request_seconds",
            "Latency of Bybit REST requests.",
            ["endpoint"],
            registry=self.registry,
        )
        self.bybit_ws_reconnect_total = Counter(
            "tb_bybit_ws_reconnect_total",
            "Number of Bybit websocket reconnects.",
            ["scope"],
            registry=self.registry,
        )
        self.market_events_total = Counter(
            "tb_market_events_total",
            "Number of normalized market events.",
            ["event_type"],
            registry=self.registry,
        )
        self.market_event_lag_seconds = Histogram(
            "tb_market_event_lag_seconds",
            "Event lag between exchange timestamp and ingestion.",
            ["event_type"],
            registry=self.registry,
        )
        self.private_state_sync_total = Counter(
            "tb_private_state_sync_total",
            "Number of private state sync runs.",
            registry=self.registry,
        )
        self.private_state_sync_fail_total = Counter(
            "tb_private_state_sync_fail_total",
            "Number of failed private state sync runs.",
            registry=self.registry,
        )
        self.parquet_flush_total = Counter(
            "tb_parquet_flush_total",
            "Number of parquet flushes.",
            registry=self.registry,
        )
        self.parquet_flush_fail_total = Counter(
            "tb_parquet_flush_fail_total",
            "Number of parquet flush failures.",
            registry=self.registry,
        )
        self.redis_publish_fail_total = Counter(
            "tb_redis_publish_fail_total",
            "Number of Redis latest-cache publish failures.",
            registry=self.registry,
        )
        self.runtime_runs_total = Counter(
            "tb_runtime_runs_total",
            "Number of simulated/runtime runs.",
            ["run_mode"],
            registry=self.registry,
        )
        self.strategy_intents_total = Counter(
            "tb_strategy_intents_total",
            "Number of generated strategy intents.",
            ["strategy_name", "action"],
            registry=self.registry,
        )
        self.risk_decisions_total = Counter(
            "tb_risk_decisions_total",
            "Number of risk decisions.",
            ["decision"],
            registry=self.registry,
        )
        self.execution_plans_total = Counter(
            "tb_execution_plans_total",
            "Number of execution plans submitted to a venue.",
            ["venue"],
            registry=self.registry,
        )
        self.paper_orders_total = Counter(
            "tb_paper_orders_total",
            "Number of paper orders by status and type.",
            ["status", "order_type"],
            registry=self.registry,
        )
        self.paper_fills_total = Counter(
            "tb_paper_fills_total",
            "Number of paper fills by liquidity type.",
            ["liquidity_type"],
            registry=self.registry,
        )
        self.paper_fill_latency_seconds = Histogram(
            "tb_paper_fill_latency_seconds",
            "Latency between order submit and simulated fill.",
            registry=self.registry,
        )
        self.paper_realized_pnl = Gauge(
            "tb_paper_realized_pnl",
            "Latest paper realized PnL.",
            registry=self.registry,
        )
        self.paper_unrealized_pnl = Gauge(
            "tb_paper_unrealized_pnl",
            "Latest paper unrealized PnL.",
            registry=self.registry,
        )
        self.replay_events_total = Counter(
            "tb_replay_events_total",
            "Number of replay/backtest events processed.",
            ["event_type"],
            registry=self.registry,
        )
        self.backtest_duration_seconds = Histogram(
            "tb_backtest_duration_seconds",
            "Backtest wall-clock duration.",
            registry=self.registry,
        )
        self.backtest_max_drawdown = Gauge(
            "tb_backtest_max_drawdown",
            "Latest backtest max drawdown.",
            registry=self.registry,
        )
        self.operational_alerts_sent_total = Counter(
            "tb_operational_alerts_sent_total",
            "Number of operational alerts sent.",
            ["channel", "severity", "kind"],
            registry=self.registry,
        )
        self.operational_alerts_failed_total = Counter(
            "tb_operational_alerts_failed_total",
            "Number of failed operational alerts.",
            ["channel", "severity", "kind"],
            registry=self.registry,
        )
        self.telegram_commands_total = Counter(
            "tb_telegram_commands_total",
            "Number of Telegram command attempts.",
            ["command", "outcome"],
            registry=self.registry,
        )
        self.live_orders_submitted_total = Counter(
            "tb_live_orders_submitted_total",
            "Number of live order submit attempts by status.",
            ["status"],
            registry=self.registry,
        )
        self.live_order_submit_seconds = Histogram(
            "tb_live_order_submit_seconds",
            "Latency of live order submit requests.",
            registry=self.registry,
        )
        self.live_order_cancels_total = Counter(
            "tb_live_order_cancels_total",
            "Number of live order cancel attempts by status.",
            ["status"],
            registry=self.registry,
        )
        self.live_private_ws_gap_total = Counter(
            "tb_live_private_ws_gap_total",
            "Number of live private websocket gap/reconnect incidents.",
            registry=self.registry,
        )
        self.live_private_ws_last_event_age_seconds = Gauge(
            "tb_live_private_ws_last_event_age_seconds",
            "Age of the latest private websocket event in seconds.",
            registry=self.registry,
        )
        self.live_rest_resync_total = Counter(
            "tb_live_rest_resync_total",
            "Number of live REST resync attempts by status.",
            ["status"],
            registry=self.registry,
        )
        self.live_recovery_total = Counter(
            "tb_live_recovery_total",
            "Number of startup live recovery outcomes.",
            ["outcome"],
            registry=self.registry,
        )
        self.live_rollout_guard_total = Counter(
            "tb_live_rollout_guard_total",
            "Number of rollout guard rejections in live mode.",
            ["reason"],
            registry=self.registry,
        )
        self.live_total_exposure_usdt = Gauge(
            "tb_live_total_exposure_usdt",
            "Estimated live total open exposure in USDT.",
            registry=self.registry,
        )

    def record_app_start(self) -> None:
        self.app_start_total.inc()

    def record_healthcheck(self) -> None:
        self.healthcheck_total.inc()

    def record_readiness_failure(self) -> None:
        self.readiness_fail_total.inc()

    def record_postgres_ping(self, seconds: float) -> None:
        self.postgres_ping_seconds.observe(seconds)

    def record_redis_ping(self, seconds: float) -> None:
        self.redis_ping_seconds.observe(seconds)

    def record_config_validation_failure(self) -> None:
        self.config_validation_fail_total.inc()

    def record_capture_run(self) -> None:
        self.capture_run_total.inc()

    def record_bybit_rest_request(self, endpoint: str, status: str, seconds: float) -> None:
        self.bybit_rest_requests_total.labels(endpoint=endpoint, status=status).inc()
        self.bybit_rest_request_seconds.labels(endpoint=endpoint).observe(seconds)

    def record_bybit_ws_reconnect(self, scope: str) -> None:
        self.bybit_ws_reconnect_total.labels(scope=scope).inc()

    def record_market_event(self, event_type: str, lag_seconds: float | None = None) -> None:
        self.market_events_total.labels(event_type=event_type).inc()
        if lag_seconds is not None:
            self.market_event_lag_seconds.labels(event_type=event_type).observe(max(lag_seconds, 0.0))

    def record_private_state_sync(self, *, success: bool) -> None:
        if success:
            self.private_state_sync_total.inc()
        else:
            self.private_state_sync_fail_total.inc()

    def record_parquet_flush(self, *, success: bool) -> None:
        if success:
            self.parquet_flush_total.inc()
        else:
            self.parquet_flush_fail_total.inc()

    def record_redis_publish_failure(self) -> None:
        self.redis_publish_fail_total.inc()

    def record_runtime_run(self, run_mode: str) -> None:
        self.runtime_runs_total.labels(run_mode=run_mode).inc()

    def record_strategy_intent(self, strategy_name: str, action: str) -> None:
        self.strategy_intents_total.labels(strategy_name=strategy_name, action=action).inc()

    def record_risk_decision(self, decision: str) -> None:
        self.risk_decisions_total.labels(decision=decision).inc()

    def record_execution_plan(self, venue: str) -> None:
        self.execution_plans_total.labels(venue=venue).inc()

    def record_paper_order(self, status: str, order_type: str) -> None:
        self.paper_orders_total.labels(status=status, order_type=order_type).inc()

    def record_paper_fill(self, liquidity_type: str, latency_seconds: float) -> None:
        self.paper_fills_total.labels(liquidity_type=liquidity_type).inc()
        self.paper_fill_latency_seconds.observe(latency_seconds)

    def set_paper_realized_pnl(self, value: float) -> None:
        self.paper_realized_pnl.set(value)

    def set_paper_unrealized_pnl(self, value: float) -> None:
        self.paper_unrealized_pnl.set(value)

    def record_replay_event(self, event_type: str) -> None:
        self.replay_events_total.labels(event_type=event_type).inc()

    def record_backtest_duration(self, seconds: float) -> None:
        self.backtest_duration_seconds.observe(seconds)

    def set_backtest_max_drawdown(self, value: float) -> None:
        self.backtest_max_drawdown.set(value)

    def record_operational_alert(self, *, channel: str, severity: str, kind: str, success: bool) -> None:
        counter = self.operational_alerts_sent_total if success else self.operational_alerts_failed_total
        counter.labels(channel=channel, severity=severity, kind=kind).inc()

    def record_telegram_command(self, *, command: str, outcome: str) -> None:
        self.telegram_commands_total.labels(command=command, outcome=outcome).inc()

    def record_live_order_submit(self, *, status: str, seconds: float | None = None) -> None:
        self.live_orders_submitted_total.labels(status=status).inc()
        if seconds is not None:
            self.live_order_submit_seconds.observe(max(seconds, 0.0))

    def record_live_order_cancel(self, *, status: str) -> None:
        self.live_order_cancels_total.labels(status=status).inc()

    def record_live_private_ws_gap(self) -> None:
        self.live_private_ws_gap_total.inc()

    def set_live_private_ws_last_event_age(self, value: float) -> None:
        self.live_private_ws_last_event_age_seconds.set(max(value, 0.0))

    def record_live_rest_resync(self, *, status: str) -> None:
        self.live_rest_resync_total.labels(status=status).inc()

    def record_live_recovery(self, *, outcome: str) -> None:
        self.live_recovery_total.labels(outcome=outcome).inc()

    def record_live_rollout_guard(self, *, reason: str) -> None:
        self.live_rollout_guard_total.labels(reason=reason).inc()

    def set_live_total_exposure_usdt(self, value: float) -> None:
        self.live_total_exposure_usdt.set(max(value, 0.0))

    def render(self) -> bytes:
        return generate_latest(self.registry)

    @property
    def content_type(self) -> str:
        return CONTENT_TYPE_LATEST
