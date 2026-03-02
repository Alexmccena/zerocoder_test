from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, generate_latest


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

    def render(self) -> bytes:
        return generate_latest(self.registry)

    @property
    def content_type(self) -> str:
        return CONTENT_TYPE_LATEST
