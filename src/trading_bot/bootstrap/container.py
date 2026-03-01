from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from structlog.stdlib import BoundLogger

from trading_bot.bootstrap.settings import BootstrapSettings
from trading_bot.config.loader import load_app_config
from trading_bot.config.schema import AppSettings
from trading_bot.domain.enums import ServiceStatus
from trading_bot.domain.models import HealthReport
from trading_bot.observability.health import HealthChecker
from trading_bot.observability.logging import configure_logging, shutdown_logging
from trading_bot.observability.metrics import AppMetrics
from trading_bot.storage.db import build_async_engine, create_session_factory, ping_database
from trading_bot.storage.redis import build_redis_client, ping_redis, publish_runtime_state


@dataclass(slots=True)
class AppContainer:
    bootstrap: BootstrapSettings
    config: AppSettings
    config_hash: str
    logger: BoundLogger
    db_engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    redis_client: Redis
    metrics: AppMetrics
    health_checker: HealthChecker

    @classmethod
    def build(cls, bootstrap: BootstrapSettings | None = None) -> "AppContainer":
        env_settings = bootstrap or BootstrapSettings()
        loaded = load_app_config(env_settings)
        logger = configure_logging(loaded.settings.observability, loaded.settings.runtime)
        metrics = AppMetrics()
        db_engine = build_async_engine(loaded.settings.storage.postgres_dsn)
        session_factory = create_session_factory(db_engine)
        redis_client = build_redis_client(loaded.settings.storage.redis_dsn)
        health_checker = HealthChecker(
            service_name=loaded.settings.runtime.service_name,
            environment=loaded.settings.runtime.environment,
            metrics=metrics,
            db_ping=lambda: ping_database(db_engine),
            redis_ping=lambda: ping_redis(redis_client),
        )
        return cls(
            bootstrap=env_settings,
            config=loaded.settings,
            config_hash=loaded.fingerprint,
            logger=logger,
            db_engine=db_engine,
            session_factory=session_factory,
            redis_client=redis_client,
            metrics=metrics,
            health_checker=health_checker,
        )

    async def startup(self) -> None:
        self.metrics.record_app_start()
        report = await self.health_checker.check_health()
        if report.checks.get("redis") == ServiceStatus.OK:
            await publish_runtime_state(self.redis_client, report.status.value, self.config_hash)
        self.logger.info(
            "application_startup",
            status=report.status.value,
            config_hash=self.config_hash,
        )

    async def doctor_report(self) -> HealthReport:
        return await self.health_checker.check_readiness()

    async def shutdown(self) -> None:
        try:
            await self.redis_client.aclose()
        finally:
            await self.db_engine.dispose()
            shutdown_logging()


def build_container(bootstrap: BootstrapSettings | None = None) -> AppContainer:
    return AppContainer.build(bootstrap)
