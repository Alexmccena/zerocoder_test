from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field

from trading_bot.domain.enums import Environment, ServiceStatus
from trading_bot.domain.models import HealthReport
from trading_bot.observability.metrics import AppMetrics


def aggregate_service_status(checks: Mapping[str, ServiceStatus]) -> ServiceStatus:
    if not checks:
        return ServiceStatus.DOWN
    if checks.get("config") == ServiceStatus.DOWN:
        return ServiceStatus.DOWN
    if any(status == ServiceStatus.DOWN for status in checks.values()):
        return ServiceStatus.DEGRADED
    if any(status == ServiceStatus.DEGRADED for status in checks.values()):
        return ServiceStatus.DEGRADED
    return ServiceStatus.OK


@dataclass(slots=True)
class HealthChecker:
    service_name: str
    environment: Environment
    metrics: AppMetrics
    db_ping: Callable[[], Awaitable[float]]
    redis_ping: Callable[[], Awaitable[float]]
    last_report: HealthReport = field(
        default_factory=lambda: HealthReport(
            status=ServiceStatus.DEGRADED,
            service="trading-bot",
            environment=Environment.DEV,
            checks={
                "config": ServiceStatus.OK,
                "postgres": ServiceStatus.DEGRADED,
                "redis": ServiceStatus.DEGRADED,
            },
        )
    )

    async def check_health(self) -> HealthReport:
        self.metrics.record_healthcheck()
        checks = {"config": ServiceStatus.OK}
        try:
            self.metrics.record_postgres_ping(await self.db_ping())
            checks["postgres"] = ServiceStatus.OK
        except Exception:
            checks["postgres"] = ServiceStatus.DOWN

        try:
            self.metrics.record_redis_ping(await self.redis_ping())
            checks["redis"] = ServiceStatus.OK
        except Exception:
            checks["redis"] = ServiceStatus.DOWN

        report = HealthReport(
            status=aggregate_service_status(checks),
            service=self.service_name,
            environment=self.environment,
            checks=checks,
        )
        self.last_report = report
        return report

    async def check_readiness(self) -> HealthReport:
        report = await self.check_health()
        if report.status != ServiceStatus.OK:
            self.metrics.record_readiness_failure()
        return report
