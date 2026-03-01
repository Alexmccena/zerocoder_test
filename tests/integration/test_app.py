from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from fastapi.testclient import TestClient

from trading_bot.app import create_app
from trading_bot.domain.enums import Environment, ServiceStatus
from trading_bot.domain.models import HealthReport
from trading_bot.observability.metrics import AppMetrics


class StubHealthChecker:
    def __init__(self, *, health: HealthReport, ready: HealthReport) -> None:
        self._health = health
        self._ready = ready

    async def check_health(self) -> HealthReport:
        return self._health

    async def check_readiness(self) -> HealthReport:
        return self._ready


@dataclass
class StubContainer:
    health_checker: StubHealthChecker
    metrics: AppMetrics

    def __post_init__(self) -> None:
        self.config = SimpleNamespace(runtime=SimpleNamespace(service_name="trading-bot"))

    async def startup(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None


def test_health_endpoint_returns_200() -> None:
    container = StubContainer(
        health_checker=StubHealthChecker(
            health=HealthReport(
                status=ServiceStatus.OK,
                service="trading-bot",
                environment=Environment.TEST,
                checks={"config": ServiceStatus.OK, "postgres": ServiceStatus.OK, "redis": ServiceStatus.OK},
            ),
            ready=HealthReport(
                status=ServiceStatus.OK,
                service="trading-bot",
                environment=Environment.TEST,
                checks={"config": ServiceStatus.OK, "postgres": ServiceStatus.OK, "redis": ServiceStatus.OK},
            ),
        ),
        metrics=AppMetrics(),
    )

    with TestClient(create_app(container)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_ready_endpoint_returns_503_when_not_ready() -> None:
    container = StubContainer(
        health_checker=StubHealthChecker(
            health=HealthReport(
                status=ServiceStatus.OK,
                service="trading-bot",
                environment=Environment.TEST,
                checks={"config": ServiceStatus.OK, "postgres": ServiceStatus.OK, "redis": ServiceStatus.OK},
            ),
            ready=HealthReport(
                status=ServiceStatus.DEGRADED,
                service="trading-bot",
                environment=Environment.TEST,
                checks={"config": ServiceStatus.OK, "postgres": ServiceStatus.DOWN, "redis": ServiceStatus.OK},
            ),
        ),
        metrics=AppMetrics(),
    )

    with TestClient(create_app(container)) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["postgres"] == "down"
