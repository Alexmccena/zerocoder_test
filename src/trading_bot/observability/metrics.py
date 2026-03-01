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

    def render(self) -> bytes:
        return generate_latest(self.registry)

    @property
    def content_type(self) -> str:
        return CONTENT_TYPE_LATEST
