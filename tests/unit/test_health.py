from __future__ import annotations

from trading_bot.domain.enums import Environment, ServiceStatus
from trading_bot.observability.health import HealthChecker, aggregate_service_status
from trading_bot.observability.metrics import AppMetrics


async def ok_ping() -> float:
    return 0.01


async def failing_ping() -> float:
    raise RuntimeError("down")


def test_aggregate_service_status() -> None:
    assert aggregate_service_status({"config": ServiceStatus.OK, "postgres": ServiceStatus.OK}) == ServiceStatus.OK
    assert aggregate_service_status({"config": ServiceStatus.OK, "postgres": ServiceStatus.DOWN}) == ServiceStatus.DEGRADED
    assert aggregate_service_status({"config": ServiceStatus.DOWN}) == ServiceStatus.DOWN


async def test_health_checker_reports_degraded_when_dependency_fails() -> None:
    checker = HealthChecker(
        service_name="trading-bot",
        environment=Environment.TEST,
        metrics=AppMetrics(),
        db_ping=ok_ping,
        redis_ping=failing_ping,
    )

    report = await checker.check_readiness()

    assert report.status == ServiceStatus.DEGRADED
    assert report.checks["postgres"] == ServiceStatus.OK
    assert report.checks["redis"] == ServiceStatus.DOWN
