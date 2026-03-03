from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from structlog.stdlib import BoundLogger

from trading_bot.adapters.exchanges.bybit.capabilities import build_bybit_capabilities
from trading_bot.adapters.exchanges.bybit.normalizers import normalize_private_message, normalize_public_message
from trading_bot.adapters.exchanges.bybit.private_ws import BybitPrivateWebSocketClient
from trading_bot.adapters.exchanges.bybit.public_ws import BybitPublicWebSocketClient
from trading_bot.adapters.exchanges.bybit.rest import BybitRestClient
from trading_bot.bootstrap.settings import BootstrapSettings
from trading_bot.config.loader import load_app_config
from trading_bot.config.schema import AppSettings
from trading_bot.domain.enums import ExecutionVenueKind, RunMode, ServiceStatus
from trading_bot.domain.models import HealthReport
from trading_bot.execution.engine import ExecutionEngine
from trading_bot.marketdata.capture import CaptureService
from trading_bot.marketdata.events import MarketEvent, PrivateStateEvent
from trading_bot.marketdata.feed import BybitPublicMarketFeed
from trading_bot.marketdata.snapshots import FeatureProvider, MarketSnapshotBuilder
from trading_bot.observability.health import HealthChecker
from trading_bot.observability.logging import configure_logging, shutdown_logging
from trading_bot.observability.metrics import AppMetrics
from trading_bot.paper.venue import PaperVenue
from trading_bot.replay.feed import ReplayFeed
from trading_bot.replay.reader import ReplayReader
from trading_bot.risk.basic import BasicRiskEngine
from trading_bot.runtime.clock import BacktestClock, ReplayClock, WallClock
from trading_bot.runtime.runner import RuntimeRunner
from trading_bot.runtime.state import RuntimeStateStore
from trading_bot.storage.db import build_async_engine, create_session_factory, ping_database
from trading_bot.storage.parquet import ParquetArchiveWriter
from trading_bot.storage.redis import build_redis_client, ping_redis, publish_runtime_state
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
from trading_bot.strategies.phase3_placeholder import Phase3PlaceholderStrategy


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


@dataclass(slots=True)
class CaptureContainer:
    bootstrap: BootstrapSettings
    config: AppSettings
    config_hash: str
    logger: BoundLogger
    db_engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    redis_client: Redis
    metrics: AppMetrics
    rest_client: BybitRestClient
    public_ws_client: BybitPublicWebSocketClient
    private_ws_client: BybitPrivateWebSocketClient | None
    capture_service: CaptureService

    @classmethod
    def build(
        cls,
        bootstrap: BootstrapSettings | None = None,
        *,
        public_only: bool = False,
    ) -> "CaptureContainer":
        env_settings = bootstrap or BootstrapSettings()
        overrides = {"runtime": {"mode": "capture"}}
        if public_only:
            overrides["exchange"] = {"private_state_enabled": False}
        loaded = load_app_config(env_settings, overrides=overrides)
        logger = configure_logging(loaded.settings.observability, loaded.settings.runtime)
        metrics = AppMetrics()
        db_engine = build_async_engine(loaded.settings.storage.postgres_dsn)
        session_factory = create_session_factory(db_engine)
        redis_client = build_redis_client(loaded.settings.storage.redis_dsn)
        rest_client = BybitRestClient(
            config=loaded.settings,
            api_key=env_settings.bybit_api_key,
            api_secret=env_settings.bybit_api_secret,
            metrics=metrics,
        )
        public_ws_client = BybitPublicWebSocketClient(config=loaded.settings, metrics=metrics)
        private_ws_client = (
            BybitPrivateWebSocketClient(config=loaded.settings, rest_client=rest_client, metrics=metrics)
            if loaded.settings.exchange.private_state_enabled
            else None
        )
        archive_writer = ParquetArchiveWriter(
            root=Path(loaded.settings.storage.market_archive_root),
            compression=loaded.settings.storage.parquet_compression,
            flush_rows=loaded.settings.storage.parquet_flush_rows,
            flush_seconds=loaded.settings.storage.parquet_flush_seconds,
            metrics=metrics,
        )
        run_sessions = RunSessionRepository(session_factory)
        config_snapshots = ConfigSnapshotRepository(session_factory)
        instruments = InstrumentRepository(session_factory)
        account_snapshots = AccountSnapshotRepository(session_factory)
        orders = OrderRepository(session_factory)
        fills = FillRepository(session_factory)
        positions = PositionRepository(session_factory)

        async def stream_public_events() -> AsyncIterator[MarketEvent]:
            async for message in public_ws_client.stream(loaded.settings.symbols.allowlist):
                for event in normalize_public_message(message):
                    if isinstance(event, MarketEvent):
                        yield event

        async def stream_private_events() -> AsyncIterator[PrivateStateEvent]:
            if private_ws_client is None:
                return
            async for message in private_ws_client.stream():
                for event in normalize_private_message(message):
                    if isinstance(event, PrivateStateEvent):
                        yield event

        capture_service = CaptureService(
            config=loaded.settings,
            config_hash=loaded.fingerprint,
            logger=logger,
            metrics=metrics,
            redis_client=redis_client,
            run_sessions=run_sessions,
            config_snapshots=config_snapshots,
            instruments=instruments,
            account_snapshots=account_snapshots,
            orders=orders,
            fills=fills,
            positions=positions,
            archive_writer=archive_writer,
            capabilities=build_bybit_capabilities(loaded.settings),
            fetch_instruments=lambda: rest_client.fetch_instruments(loaded.settings.symbols.allowlist),
            stream_public_events=stream_public_events,
            fetch_open_interest=rest_client.fetch_open_interest,
            fetch_funding_rate=rest_client.fetch_funding_rate,
            fetch_account_state=rest_client.fetch_account_state if loaded.settings.exchange.private_state_enabled else None,
            fetch_open_orders=(lambda: rest_client.fetch_open_orders()) if loaded.settings.exchange.private_state_enabled else None,
            fetch_positions=rest_client.fetch_positions if loaded.settings.exchange.private_state_enabled else None,
            stream_private_events=stream_private_events if loaded.settings.exchange.private_state_enabled else None,
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
            rest_client=rest_client,
            public_ws_client=public_ws_client,
            private_ws_client=private_ws_client,
            capture_service=capture_service,
        )

    async def run_capture(self, *, duration_seconds: int | None = None) -> None:
        await self.capture_service.run(duration_seconds=duration_seconds)

    async def shutdown(self) -> None:
        try:
            await self.rest_client.close()
        finally:
            try:
                await self.redis_client.aclose()
            finally:
                await self.db_engine.dispose()
                shutdown_logging()


def build_capture_container(
    bootstrap: BootstrapSettings | None = None,
    *,
    public_only: bool = False,
) -> CaptureContainer:
    return CaptureContainer.build(bootstrap, public_only=public_only)


@dataclass(slots=True)
class RuntimeContainer:
    bootstrap: BootstrapSettings
    config: AppSettings
    config_hash: str
    logger: BoundLogger
    db_engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    redis_client: Redis
    metrics: AppMetrics
    runtime_runner: RuntimeRunner

    @classmethod
    def build(
        cls,
        bootstrap: BootstrapSettings | None = None,
        *,
        mode: RunMode,
        source: str | None = None,
        start_at: str | None = None,
        end_at: str | None = None,
        speed: float | None = None,
    ) -> "RuntimeContainer":
        env_settings = bootstrap or BootstrapSettings()
        overrides: dict[str, Any] = {
            "runtime": {"mode": mode.value},
            "exchange": {"private_state_enabled": False},
        }
        if mode in {RunMode.REPLAY, RunMode.BACKTEST}:
            replay_overrides: dict[str, Any] = {
                "source_root": source,
                "start_at": start_at,
                "end_at": end_at,
            }
            if speed is not None:
                replay_overrides["speed"] = speed
            overrides["replay"] = replay_overrides
        loaded = load_app_config(env_settings, overrides=overrides)
        logger = configure_logging(loaded.settings.observability, loaded.settings.runtime)
        metrics = AppMetrics()
        db_engine = build_async_engine(loaded.settings.storage.postgres_dsn)
        session_factory = create_session_factory(db_engine)
        redis_client = build_redis_client(loaded.settings.storage.redis_dsn)

        run_sessions = RunSessionRepository(session_factory)
        config_snapshots = ConfigSnapshotRepository(session_factory)
        instruments = InstrumentRepository(session_factory)
        signal_events = SignalEventRepository(session_factory)
        risk_decisions = RiskDecisionRepository(session_factory)
        orders = OrderRepository(session_factory)
        fills = FillRepository(session_factory)
        positions = PositionRepository(session_factory)
        account_snapshots = AccountSnapshotRepository(session_factory)
        pnl_snapshots = PnlSnapshotRepository(session_factory)

        if mode == RunMode.PAPER:
            rest_client = BybitRestClient(
                config=loaded.settings,
                api_key=env_settings.bybit_api_key,
                api_secret=env_settings.bybit_api_secret,
                metrics=metrics,
            )
            public_ws_client = BybitPublicWebSocketClient(config=loaded.settings, metrics=metrics)
            market_feed = BybitPublicMarketFeed(rest_client=rest_client, public_ws_client=public_ws_client)
            clock = WallClock()
            strategy_start_at = None
        else:
            strategy_start_at = loaded.settings.replay.start_at
            reader_start_at = strategy_start_at
            if reader_start_at is not None:
                reader_start_at = reader_start_at - timedelta(minutes=loaded.settings.replay.warmup_minutes)
            replay_reader = ReplayReader(
                source_root=Path(loaded.settings.replay.source_root or ""),
                start_at=reader_start_at,
                end_at=loaded.settings.replay.end_at,
                fail_on_gap=loaded.settings.replay.fail_on_gap,
                max_gap_seconds=loaded.settings.replay.max_gap_seconds,
            )
            market_feed = ReplayFeed(reader=replay_reader, strategy_start_at=strategy_start_at)
            clock = ReplayClock(speed=loaded.settings.replay.speed) if mode == RunMode.REPLAY else BacktestClock()

        snapshot_builder = MarketSnapshotBuilder(stale_after_seconds=loaded.settings.risk.stale_market_data_seconds)
        feature_provider = FeatureProvider(timeframe=loaded.settings.strategy.default_timeframe)
        runtime_state = RuntimeStateStore(run_mode=mode, execution_venue=ExecutionVenueKind.PAPER)
        strategy = Phase3PlaceholderStrategy(config=loaded.settings, runtime_state_provider=lambda: runtime_state.state)
        risk_engine = BasicRiskEngine(config=loaded.settings)
        execution_engine = ExecutionEngine(venue=PaperVenue(config=loaded.settings, metrics=metrics))
        runtime_runner = RuntimeRunner(
            config=loaded.settings,
            config_hash=loaded.fingerprint,
            logger=logger,
            metrics=metrics,
            redis_client=redis_client,
            market_feed=market_feed,
            clock=clock,
            state_store=runtime_state,
            snapshot_builder=snapshot_builder,
            feature_provider=feature_provider,
            strategy=strategy,
            risk_engine=risk_engine,
            execution_engine=execution_engine,
            run_sessions=run_sessions,
            config_snapshots=config_snapshots,
            instruments=instruments,
            signal_events=signal_events,
            risk_decisions=risk_decisions,
            orders=orders,
            fills=fills,
            positions=positions,
            account_snapshots=account_snapshots,
            pnl_snapshots=pnl_snapshots,
            strategy_start_at=strategy_start_at,
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
            runtime_runner=runtime_runner,
        )

    async def run_runtime(self, *, duration_seconds: int | None = None, summary_out: Path | None = None) -> dict[str, object]:
        return await self.runtime_runner.run(duration_seconds=duration_seconds, summary_out=summary_out)

    async def shutdown(self) -> None:
        try:
            await self.redis_client.aclose()
        finally:
            await self.db_engine.dispose()
            shutdown_logging()


def build_runtime_container(
    bootstrap: BootstrapSettings | None = None,
    *,
    mode: RunMode,
    source: str | None = None,
    start_at: str | None = None,
    end_at: str | None = None,
    speed: float | None = None,
) -> RuntimeContainer:
    return RuntimeContainer.build(
        bootstrap,
        mode=mode,
        source=source,
        start_at=start_at,
        end_at=end_at,
        speed=speed,
    )
