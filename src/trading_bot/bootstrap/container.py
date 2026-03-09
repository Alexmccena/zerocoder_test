from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any
from contextlib import suppress
import asyncio
from decimal import Decimal

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from structlog.stdlib import BoundLogger

from trading_bot.adapters.exchanges.binance.capabilities import build_binance_capabilities
from trading_bot.adapters.exchanges.binance.normalizers import (
    normalize_private_message as normalize_binance_private_message,
)
from trading_bot.adapters.exchanges.binance.normalizers import (
    normalize_public_message as normalize_binance_public_message,
)
from trading_bot.adapters.exchanges.binance.private_ws import BinancePrivateWebSocketClient
from trading_bot.adapters.exchanges.binance.public_ws import BinancePublicWebSocketClient
from trading_bot.adapters.exchanges.binance.rest import BinanceRestClient
from trading_bot.adapters.exchanges.bybit.capabilities import build_bybit_capabilities
from trading_bot.adapters.exchanges.bybit.normalizers import normalize_private_message as normalize_bybit_private_message
from trading_bot.adapters.exchanges.bybit.normalizers import normalize_public_message as normalize_bybit_public_message
from trading_bot.adapters.exchanges.bybit.private_ws import BybitPrivateWebSocketClient
from trading_bot.adapters.exchanges.bybit.public_ws import BybitPublicWebSocketClient
from trading_bot.adapters.exchanges.bybit.rest import BybitRestClient
from trading_bot.alerts.service import TelegramAlertService
from trading_bot.bootstrap.settings import BootstrapSettings
from trading_bot.config.loader import load_app_config
from trading_bot.config.schema import AppSettings
from trading_bot.domain.enums import ExchangeName, ExecutionVenueKind, RunMode, ServiceStatus
from trading_bot.domain.models import HealthReport
from trading_bot.execution.engine import ExecutionEngine
from trading_bot.live.venue import LiveVenue
from trading_bot.llm import LLMAdvisoryService, OpenRouterProvider
from trading_bot.marketdata.capture import CaptureService
from trading_bot.marketdata.events import MarketEvent, PrivateStateEvent
from trading_bot.marketdata.feed import ExchangePublicMarketFeed
from trading_bot.marketdata.snapshots import FeatureProvider, MarketSnapshotBuilder
from trading_bot.observability.health import HealthChecker
from trading_bot.observability.logging import configure_logging, shutdown_logging
from trading_bot.observability.metrics import AppMetrics
from trading_bot.paper.venue import PaperVenue
from trading_bot.replay.feed import ReplayFeed
from trading_bot.replay.reader import ReplayReader
from trading_bot.risk.basic import BasicRiskEngine
from trading_bot.runtime.clock import BacktestClock, ReplayClock, WallClock
from trading_bot.runtime.control import RuntimeControlPlane
from trading_bot.runtime.grid_runtime import GridRuntime
from trading_bot.runtime.runner import RuntimeRunner
from trading_bot.runtime.state import RuntimeStateStore
from trading_bot.storage.db import build_async_engine, create_session_factory, ping_database
from trading_bot.storage.parquet import ParquetArchiveWriter
from trading_bot.storage.redis import build_redis_client, ping_redis, publish_runtime_state
from trading_bot.storage.repositories import (
    AccountSnapshotRepository,
    ConfigSnapshotRepository,
    FillRepository,
    GridEventRepository,
    GridOrderLinkRepository,
    GridPairProfileRepository,
    GridPairSnapshotRepository,
    InstrumentRepository,
    LLMAdviceRepository,
    OrderRepository,
    PnlSnapshotRepository,
    PositionRepository,
    RiskDecisionRepository,
    RunSessionRepository,
    SignalEventRepository,
)
from trading_bot.strategies import build_strategy
from trading_bot.timeframes import canonicalize_interval, interval_to_bybit


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


type ExchangeRestClient = BybitRestClient | BinanceRestClient
type ExchangePublicWsClient = BybitPublicWebSocketClient | BinancePublicWebSocketClient
type ExchangePrivateWsClient = BybitPrivateWebSocketClient | BinancePrivateWebSocketClient
type MessageNormalizer = Callable[[dict[str, Any]], list[object]]


@dataclass(slots=True)
class ExchangeClients:
    rest_client: ExchangeRestClient
    public_ws_client: ExchangePublicWsClient
    private_ws_client: ExchangePrivateWsClient | None
    public_message_normalizer: MessageNormalizer
    private_message_normalizer: MessageNormalizer
    capabilities_builder: Callable[[AppSettings], Any]
    interval_mapper: Callable[[str], str]


def _build_exchange_clients(
    *,
    settings: AppSettings,
    env_settings: BootstrapSettings,
    metrics: AppMetrics,
    with_private_state: bool,
) -> ExchangeClients:
    exchange = settings.exchange.primary
    if exchange == ExchangeName.BYBIT:
        rest_client = BybitRestClient(
            config=settings,
            api_key=env_settings.bybit_api_key,
            api_secret=env_settings.bybit_api_secret,
            metrics=metrics,
        )
        public_ws_client = BybitPublicWebSocketClient(config=settings, metrics=metrics)
        private_ws_client: ExchangePrivateWsClient | None = None
        if with_private_state:
            private_ws_client = BybitPrivateWebSocketClient(
                config=settings,
                rest_client=rest_client,
                metrics=metrics,
            )
        return ExchangeClients(
            rest_client=rest_client,
            public_ws_client=public_ws_client,
            private_ws_client=private_ws_client,
            public_message_normalizer=normalize_bybit_public_message,
            private_message_normalizer=normalize_bybit_private_message,
            capabilities_builder=build_bybit_capabilities,
            interval_mapper=interval_to_bybit,
        )

    if exchange == ExchangeName.BINANCE:
        rest_client = BinanceRestClient(
            config=settings,
            api_key=env_settings.binance_api_key,
            api_secret=env_settings.binance_api_secret,
            metrics=metrics,
        )
        public_ws_client = BinancePublicWebSocketClient(config=settings, metrics=metrics)
        private_ws_client: ExchangePrivateWsClient | None = None
        if with_private_state:
            private_ws_client = BinancePrivateWebSocketClient(
                config=settings,
                rest_client=rest_client,
                metrics=metrics,
            )
        return ExchangeClients(
            rest_client=rest_client,
            public_ws_client=public_ws_client,
            private_ws_client=private_ws_client,
            public_message_normalizer=normalize_binance_public_message,
            private_message_normalizer=normalize_binance_private_message,
            capabilities_builder=build_binance_capabilities,
            interval_mapper=canonicalize_interval,
        )

    raise RuntimeError(f"Unsupported exchange.primary: {exchange.value}")


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
    rest_client: ExchangeRestClient
    public_ws_client: ExchangePublicWsClient
    private_ws_client: ExchangePrivateWsClient | None
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
        exchange_clients = _build_exchange_clients(
            settings=loaded.settings,
            env_settings=env_settings,
            metrics=metrics,
            with_private_state=loaded.settings.exchange.private_state_enabled,
        )
        rest_client = exchange_clients.rest_client
        public_ws_client = exchange_clients.public_ws_client
        private_ws_client = exchange_clients.private_ws_client
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
                for event in exchange_clients.public_message_normalizer(message):
                    if isinstance(event, MarketEvent):
                        yield event

        async def stream_private_events() -> AsyncIterator[PrivateStateEvent]:
            if private_ws_client is None:
                return
            async for message in private_ws_client.stream():
                for event in exchange_clients.private_message_normalizer(message):
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
            capabilities=exchange_clients.capabilities_builder(loaded.settings),
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
    control_plane: RuntimeControlPlane
    telegram_service: TelegramAlertService | None = None
    llm_service: LLMAdvisoryService | None = None
    live_rest_client: ExchangeRestClient | None = None
    live_private_ws_client: ExchangePrivateWsClient | None = None

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
        overrides: dict[str, Any] = {"runtime": {"mode": mode.value}}
        if mode in {RunMode.PAPER, RunMode.REPLAY, RunMode.BACKTEST}:
            overrides["exchange"] = {"private_state_enabled": False}
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
        llm_advice = LLMAdviceRepository(session_factory)
        grid_pair_profiles = GridPairProfileRepository(session_factory)
        grid_pair_snapshots = GridPairSnapshotRepository(session_factory)
        grid_order_links = GridOrderLinkRepository(session_factory)
        grid_events = GridEventRepository(session_factory)

        snapshot_builder = MarketSnapshotBuilder(stale_after_seconds=loaded.settings.risk.stale_market_data_seconds)
        feature_provider = FeatureProvider(config=loaded.settings)
        live_rest_client: ExchangeRestClient | None = None
        live_private_ws_client: ExchangePrivateWsClient | None = None
        exchange_clients: ExchangeClients | None = None

        if mode in {RunMode.PAPER, RunMode.LIVE}:
            exchange_clients = _build_exchange_clients(
                settings=loaded.settings,
                env_settings=env_settings,
                metrics=metrics,
                with_private_state=mode == RunMode.LIVE and loaded.settings.exchange.private_state_enabled,
            )
            market_feed = ExchangePublicMarketFeed(
                rest_client=exchange_clients.rest_client,
                public_ws_client=exchange_clients.public_ws_client,
                public_message_normalizer=exchange_clients.public_message_normalizer,
                interval_mapper=exchange_clients.interval_mapper,
            )
            if mode == RunMode.LIVE:
                live_rest_client = exchange_clients.rest_client
                live_private_ws_client = exchange_clients.private_ws_client
            clock = WallClock()
            strategy_start_at = None
        else:
            strategy_start_at = loaded.settings.replay.start_at
            reader_start_at = strategy_start_at
            if reader_start_at is not None:
                effective_warmup = max(loaded.settings.replay.warmup_minutes, feature_provider.required_warmup_minutes())
                reader_start_at = reader_start_at - timedelta(minutes=effective_warmup)
            replay_reader = ReplayReader(
                source_root=Path(loaded.settings.replay.source_root or ""),
                start_at=reader_start_at,
                end_at=loaded.settings.replay.end_at,
                fail_on_gap=loaded.settings.replay.fail_on_gap,
                max_gap_seconds=loaded.settings.replay.max_gap_seconds,
            )
            market_feed = ReplayFeed(reader=replay_reader, strategy_start_at=strategy_start_at)
            clock = ReplayClock(speed=loaded.settings.replay.speed) if mode == RunMode.REPLAY else BacktestClock()

        execution_venue_kind = ExecutionVenueKind.LIVE if mode == RunMode.LIVE else ExecutionVenueKind.PAPER
        runtime_state = RuntimeStateStore(run_mode=mode, execution_venue=execution_venue_kind)
        control_plane = RuntimeControlPlane(config=loaded.settings, state_store=runtime_state)
        strategy = build_strategy(config=loaded.settings, runtime_state_provider=lambda: runtime_state.state)
        risk_engine = BasicRiskEngine(config=loaded.settings)
        if mode == RunMode.LIVE:
            if exchange_clients is None or live_rest_client is None or live_private_ws_client is None:
                raise RuntimeError("live runtime requires private exchange clients")
            venue = LiveVenue(
                config=loaded.settings,
                metrics=metrics,
                rest_client=live_rest_client,
                private_ws_client=live_private_ws_client,
                private_message_normalizer=exchange_clients.private_message_normalizer,
            )
        else:
            venue = PaperVenue(config=loaded.settings, metrics=metrics)
        execution_engine = ExecutionEngine(config=loaded.settings, venue=venue)
        grid_runtime: GridRuntime | None = None
        if loaded.settings.strategy.name == "grid_dca_v1":
            grid_runtime = GridRuntime(
                config=loaded.settings,
                strategy=strategy,
                state_store=runtime_state,
                execution_engine=execution_engine,
                metrics=metrics,
                profiles_repo=grid_pair_profiles,
                snapshots_repo=grid_pair_snapshots,
                links_repo=grid_order_links,
                events_repo=grid_events,
            )
        llm_service: LLMAdvisoryService | None = None
        if loaded.settings.llm.enabled and loaded.settings.llm.provider == "openrouter":
            if env_settings.openrouter_api_key:
                llm_service = LLMAdvisoryService(
                    config=loaded.settings.llm,
                    logger=logger,
                    metrics=metrics,
                    repository=llm_advice,
                    provider=OpenRouterProvider(
                        api_key=env_settings.openrouter_api_key,
                        base_url=env_settings.openrouter_base_url,
                        http_referer=env_settings.openrouter_http_referer,
                        app_name=env_settings.openrouter_app_name,
                    ),
                )
            else:
                logger.warning("llm_openrouter_missing_api_key")
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
            control_plane=control_plane,
            llm_service=llm_service,
            grid_runtime=grid_runtime,
        )
        telegram_service = None
        if mode in {RunMode.PAPER, RunMode.LIVE} and loaded.settings.alerts.telegram.enabled and env_settings.telegram_bot_token is not None:
            telegram_service = TelegramAlertService(
                config=loaded.settings,
                token=env_settings.telegram_bot_token,
                logger=logger,
                metrics=metrics,
                control_plane=control_plane,
                llm_service=llm_service,
                grid_runtime=grid_runtime,
            )
            runtime_runner.alert_sink = telegram_service
            if llm_service is not None:
                llm_service.set_alert_sink(telegram_service)
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
            control_plane=control_plane,
            telegram_service=telegram_service,
            llm_service=llm_service,
            live_rest_client=live_rest_client,
            live_private_ws_client=live_private_ws_client,
        )

    async def run_runtime(self, *, duration_seconds: int | None = None, summary_out: Path | None = None) -> dict[str, object]:
        if self.telegram_service is None:
            return await self.runtime_runner.run(duration_seconds=duration_seconds, summary_out=summary_out)

        poll_task = asyncio.create_task(self.telegram_service.run())
        try:
            return await self.runtime_runner.run(duration_seconds=duration_seconds, summary_out=summary_out)
        finally:
            poll_task.cancel()
            with suppress(asyncio.CancelledError):
                await poll_task

    async def live_preflight(self) -> dict[str, object]:
        if self.config.runtime.mode != RunMode.LIVE:
            raise RuntimeError("live-preflight requires runtime.mode=live")
        if self.live_rest_client is None or self.live_private_ws_client is None:
            raise RuntimeError("live-preflight dependencies are not initialized")
        if not self.config.exchange.private_state_enabled:
            raise RuntimeError("exchange.private_state_enabled must be true for live-preflight")

        instruments, account, open_orders, open_positions = await asyncio.gather(
            self.live_rest_client.fetch_instruments(self.config.live.symbol_allowlist),
            self.live_rest_client.fetch_account_state(),
            self.live_rest_client.fetch_open_orders(),
            self.live_rest_client.fetch_positions(),
        )
        ws_auth_ok = await self.live_private_ws_client.probe_auth(timeout_seconds=5.0)
        if not ws_auth_ok:
            raise RuntimeError(f"{self.config.exchange.primary.value} private websocket auth probe failed")

        instrument_by_symbol = {instrument.symbol: instrument for instrument in instruments}
        missing_symbols = [symbol for symbol in self.config.live.symbol_allowlist if symbol not in instrument_by_symbol]
        if missing_symbols:
            raise RuntimeError(f"live.symbol_allowlist symbols not available: {','.join(missing_symbols)}")

        notional_violations: list[str] = []
        for symbol in self.config.live.symbol_allowlist:
            instrument = instrument_by_symbol[symbol]
            if instrument.min_notional is not None and self.config.live.max_order_notional_usdt < instrument.min_notional:
                notional_violations.append(symbol)
        if notional_violations:
            raise RuntimeError(
                "live.max_order_notional_usdt below instrument min_notional for: "
                + ",".join(notional_violations)
            )

        total_exposure = Decimal("0")
        open_position_rows: list[dict[str, str]] = []
        for position in open_positions:
            if position.status != "open" or position.quantity <= 0:
                continue
            reference_price = position.mark_price or position.last_price or position.entry_price
            exposure = position.quantity * reference_price
            total_exposure += exposure
            open_position_rows.append(
                {
                    "symbol": position.symbol,
                    "side": position.side,
                    "quantity": str(position.quantity),
                    "notional_usdt": str(exposure),
                }
            )

        return {
            "mode": "live",
            "network": "testnet" if self.config.exchange.testnet else "mainnet",
            "execution_enabled": self.config.live.execution_enabled,
            "allow_mainnet": self.config.live.allow_mainnet,
            "symbol_allowlist": list(self.config.live.symbol_allowlist),
            "caps": {
                "max_order_notional_usdt": str(self.config.live.max_order_notional_usdt),
                "max_position_notional_usdt": str(self.config.live.max_position_notional_usdt),
                "max_total_exposure_usdt": str(self.config.live.max_total_exposure_usdt),
                "private_state_stale_after_seconds": self.config.live.private_state_stale_after_seconds,
            },
            "account": {
                "equity": str(account.equity),
                "available_balance": str(account.available_balance),
            },
            "open_orders": len(open_orders),
            "open_positions": open_position_rows,
            "total_exposure_usdt": str(total_exposure),
            "ws_auth_ok": ws_auth_ok,
        }

    async def shutdown(self) -> None:
        try:
            if self.telegram_service is not None:
                await self.telegram_service.close()
            if self.llm_service is not None:
                with suppress(Exception):
                    await self.llm_service.stop()
            with suppress(Exception):
                await self.runtime_runner.execution_engine.close()
            with suppress(Exception):
                await self.runtime_runner.market_feed.close()
            if self.live_rest_client is not None:
                with suppress(Exception):
                    await self.live_rest_client.close()
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
