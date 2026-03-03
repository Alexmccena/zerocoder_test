from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import structlog

from trading_bot.config.schema import AppSettings
from trading_bot.domain.enums import ExecutionVenueKind, RunMode
from trading_bot.execution.engine import ExecutionEngine
from trading_bot.marketdata.snapshots import FeatureProvider, MarketSnapshotBuilder
from trading_bot.observability.metrics import AppMetrics
from trading_bot.paper.venue import PaperVenue
from trading_bot.replay.feed import ReplayFeed
from trading_bot.replay.reader import ReplayReader
from trading_bot.risk.basic import BasicRiskEngine
from trading_bot.runtime.clock import BacktestClock
from trading_bot.runtime.runner import RuntimeRunner
from trading_bot.runtime.state import RuntimeStateStore
from trading_bot.strategies.phase3_placeholder import Phase3PlaceholderStrategy


def _write_parquet(root: Path, *, event_type: str, symbol: str, rows: list[dict[str, Any]]) -> None:
    path = root / f"exchange=bybit/event_type={event_type}/date=2026-03-03/hour=00/symbol={symbol}"
    path.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path / "part-00000.parquet")


def _build_archive(root: Path) -> None:
    common = {
        "exchange_name": "bybit",
        "symbol": "BTCUSDT",
        "received_at": "2026-03-03T00:00:00+00:00",
        "raw_payload": json.dumps({}),
    }
    _write_parquet(
        root,
        event_type="orderbook",
        symbol="BTCUSDT",
        rows=[
            {
                **common,
                "event_type": "orderbook",
                "event_ts": "2026-03-03T00:00:00+00:00",
                "depth": 50,
                "sequence": 1,
                "update_id": 1,
                "is_snapshot": True,
                "bids": [{"price": "99.99", "size": "3"}],
                "asks": [{"price": "100.00", "size": "3"}],
            },
            {
                **common,
                "event_type": "orderbook",
                "event_ts": "2026-03-03T00:01:00+00:00",
                "depth": 50,
                "sequence": 2,
                "update_id": 2,
                "is_snapshot": False,
                "bids": [{"price": "101.99", "size": "6"}],
                "asks": [{"price": "102.00", "size": "2"}],
            },
            {
                **common,
                "event_type": "orderbook",
                "event_ts": "2026-03-03T00:02:00+00:00",
                "depth": 50,
                "sequence": 3,
                "update_id": 3,
                "is_snapshot": False,
                "bids": [{"price": "98.99", "size": "2"}],
                "asks": [{"price": "99.00", "size": "6"}],
            },
        ],
    )
    _write_parquet(
        root,
        event_type="kline",
        symbol="BTCUSDT",
        rows=[
            {
                **common,
                "event_type": "kline",
                "event_ts": "2026-03-03T00:00:00+00:00",
                "interval": "1m",
                "start_at": "2026-03-02T23:59:00+00:00",
                "end_at": "2026-03-03T00:00:00+00:00",
                "open_price": "99.80",
                "high_price": "100.20",
                "low_price": "99.70",
                "close_price": "100.00",
                "volume": "10",
                "turnover": "1000",
                "is_closed": True,
            },
            {
                **common,
                "event_type": "kline",
                "event_ts": "2026-03-03T00:01:00+00:00",
                "interval": "1m",
                "start_at": "2026-03-03T00:00:00+00:00",
                "end_at": "2026-03-03T00:01:00+00:00",
                "open_price": "100.00",
                "high_price": "102.20",
                "low_price": "99.90",
                "close_price": "102.00",
                "volume": "12",
                "turnover": "1220",
                "is_closed": True,
            },
            {
                **common,
                "event_type": "kline",
                "event_ts": "2026-03-03T00:02:00+00:00",
                "interval": "1m",
                "start_at": "2026-03-03T00:01:00+00:00",
                "end_at": "2026-03-03T00:02:00+00:00",
                "open_price": "102.00",
                "high_price": "102.10",
                "low_price": "98.80",
                "close_price": "99.00",
                "volume": "15",
                "turnover": "1485",
                "is_closed": True,
            },
        ],
    )


def _build_settings(source_root: Path) -> AppSettings:
    return AppSettings.model_validate(
        {
            "runtime": {
                "service_name": "trading-bot",
                "mode": "backtest",
                "environment": "test",
            },
            "exchange": {
                "primary": "bybit",
                "market_type": "linear_perp",
                "position_mode": "one_way",
                "account_alias": "default",
                "testnet": True,
            },
            "symbols": {"allowlist": ["BTCUSDT"]},
            "storage": {
                "postgres_dsn": "postgresql+asyncpg://user:pass@localhost:5432/app",
                "redis_dsn": "redis://localhost:6379/0",
            },
            "observability": {"log_level": "INFO", "http_host": "127.0.0.1", "http_port": 8080},
            "execution": {
                "default_entry_type": "market",
                "market_slippage_guard_bps": 10.0,
                "max_market_data_age_ms": 10_000,
            },
            "paper": {
                "initial_equity_usdt": "10000",
                "default_order_notional_usdt": "100",
                "fill_latency_ms": 0,
            },
            "replay": {
                "source_root": str(source_root),
                "start_at": None,
                "end_at": None,
                "speed": 1.0,
                "warmup_minutes": 0,
                "fail_on_gap": True,
                "max_gap_seconds": 120,
            },
            "strategy": {
                "name": "phase3_placeholder",
                "default_timeframe": "1m",
                "placeholder_signal_threshold_bps": 8.0,
                "placeholder_min_imbalance": 0.10,
                "placeholder_max_hold_closed_klines": 3,
            },
            "risk": {
                "max_open_positions": 2,
                "risk_per_trade": 0.0025,
                "max_daily_loss": 0.015,
                "stale_market_data_seconds": 5,
                "one_position_per_symbol": True,
            },
            "llm": {"enabled": False, "provider": "none", "model_name": "", "timeout_seconds": 10},
        }
    )


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, key: str, value: str) -> None:
        self.values[key] = value


@dataclass
class InMemoryRunSessions:
    records: list[SimpleNamespace] = field(default_factory=list)

    async def create(
        self,
        *,
        run_mode: str,
        environment: str,
        status: str,
        execution_venue: str | None = None,
    ) -> SimpleNamespace:
        record = SimpleNamespace(
            id=f"run-{len(self.records) + 1}",
            run_mode=run_mode,
            environment=environment,
            execution_venue=execution_venue,
            status=status,
            summary_json=None,
        )
        self.records.append(record)
        return record

    async def mark_completed(self, run_session_id: str, *, summary_json: dict[str, Any] | None = None) -> None:
        self.records[0].status = "completed"
        self.records[0].summary_json = summary_json

    async def mark_failed(self, run_session_id: str, *, reason: str | None = None) -> None:
        self.records[0].status = "failed" if reason is None else f"failed:{reason}"


@dataclass
class InMemoryConfigSnapshots:
    items: list[dict[str, Any]] = field(default_factory=list)

    async def create(self, *, run_session_id: str | None, config_hash: str, config_json: dict[str, Any]) -> SimpleNamespace:
        item = {"run_session_id": run_session_id, "config_hash": config_hash, "config_json": config_json}
        self.items.append(item)
        return SimpleNamespace(**item)


@dataclass
class InMemoryInstruments:
    items: list[Any] = field(default_factory=list)

    async def upsert_many(self, instruments: list[Any]) -> None:
        self.items = list(instruments)


@dataclass
class InMemorySignalEvents:
    items: list[dict[str, Any]] = field(default_factory=list)

    async def create(
        self,
        *,
        run_session_id: str,
        symbol: str,
        strategy_name: str,
        signal_type: str,
        payload_json: dict[str, Any],
    ) -> SimpleNamespace:
        item = {
            "id": f"signal-{len(self.items) + 1}",
            "run_session_id": run_session_id,
            "symbol": symbol,
            "strategy_name": strategy_name,
            "signal_type": signal_type,
            "payload_json": payload_json,
        }
        self.items.append(item)
        return SimpleNamespace(**item)


@dataclass
class InMemoryRiskDecisions:
    items: list[dict[str, Any]] = field(default_factory=list)

    async def create(
        self,
        *,
        run_session_id: str,
        symbol: str,
        intent_id: str,
        signal_event_id: str | None,
        decision,
    ) -> SimpleNamespace:
        item = {
            "run_session_id": run_session_id,
            "symbol": symbol,
            "intent_id": intent_id,
            "signal_event_id": signal_event_id,
            "decision": decision.decision.value,
            "reasons": list(decision.reasons),
        }
        self.items.append(item)
        return SimpleNamespace(**item)


@dataclass
class InMemoryOrders:
    current: dict[str, Any] = field(default_factory=dict)
    history: list[Any] = field(default_factory=list)

    async def update_lifecycle(self, *, run_session_id: str, order) -> None:
        stored = order.model_copy(deep=True)
        self.current[stored.order_id] = stored
        self.history.append(stored)


@dataclass
class InMemoryFills:
    items: list[Any] = field(default_factory=list)

    async def insert_if_new(self, fill) -> bool:
        self.items.append(fill.model_copy(deep=True))
        return True


@dataclass
class InMemoryPositions:
    open_positions: dict[str, Any] = field(default_factory=dict)
    history: list[Any] = field(default_factory=list)

    async def upsert_snapshot(self, *, run_session_id: str, position) -> None:
        stored = position.model_copy(deep=True)
        self.open_positions[stored.symbol] = stored
        self.history.append(stored)

    async def close_position(self, *, run_session_id: str, position) -> None:
        stored = position.model_copy(deep=True)
        self.open_positions.pop(stored.symbol, None)
        self.history.append(stored)


@dataclass
class InMemoryAccountSnapshots:
    items: list[Any] = field(default_factory=list)

    async def create_paper_snapshot(self, *, run_session_id: str, account) -> SimpleNamespace:
        stored = account.model_copy(deep=True)
        self.items.append(stored)
        return SimpleNamespace(run_session_id=run_session_id)


@dataclass
class InMemoryPnlSnapshots:
    items: list[Any] = field(default_factory=list)

    async def append(self, snapshot) -> SimpleNamespace:
        stored = snapshot.model_copy(deep=True)
        self.items.append(stored)
        return SimpleNamespace(run_session_id=stored.run_session_id)


@pytest.mark.asyncio
async def test_runtime_runner_backtest_e2e_with_replay_archive(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    _build_archive(archive_root)
    settings = _build_settings(archive_root)

    replay_reader = ReplayReader(
        source_root=archive_root,
        start_at=None,
        end_at=None,
        fail_on_gap=True,
        max_gap_seconds=120,
    )
    market_feed = ReplayFeed(reader=replay_reader, strategy_start_at=None)
    state_store = RuntimeStateStore(run_mode=RunMode.BACKTEST, execution_venue=ExecutionVenueKind.PAPER)
    snapshot_builder = MarketSnapshotBuilder(stale_after_seconds=settings.risk.stale_market_data_seconds)
    feature_provider = FeatureProvider(timeframe=settings.strategy.default_timeframe)
    strategy = Phase3PlaceholderStrategy(config=settings, runtime_state_provider=lambda: state_store.state)
    risk_engine = BasicRiskEngine(config=settings)
    execution_engine = ExecutionEngine(venue=PaperVenue(config=settings, metrics=AppMetrics()))

    run_sessions = InMemoryRunSessions()
    config_snapshots = InMemoryConfigSnapshots()
    instruments = InMemoryInstruments()
    signal_events = InMemorySignalEvents()
    risk_decisions = InMemoryRiskDecisions()
    orders = InMemoryOrders()
    fills = InMemoryFills()
    positions = InMemoryPositions()
    account_snapshots = InMemoryAccountSnapshots()
    pnl_snapshots = InMemoryPnlSnapshots()
    redis = FakeRedis()
    summary_out = tmp_path / "summary.json"

    runner = RuntimeRunner(
        config=settings,
        config_hash="test-config-hash",
        logger=structlog.get_logger("runtime-test"),
        metrics=AppMetrics(),
        redis_client=redis,  # type: ignore[arg-type]
        market_feed=market_feed,
        clock=BacktestClock(),
        state_store=state_store,
        snapshot_builder=snapshot_builder,
        feature_provider=feature_provider,
        strategy=strategy,
        risk_engine=risk_engine,
        execution_engine=execution_engine,
        run_sessions=run_sessions,  # type: ignore[arg-type]
        config_snapshots=config_snapshots,  # type: ignore[arg-type]
        instruments=instruments,  # type: ignore[arg-type]
        signal_events=signal_events,  # type: ignore[arg-type]
        risk_decisions=risk_decisions,  # type: ignore[arg-type]
        orders=orders,  # type: ignore[arg-type]
        fills=fills,  # type: ignore[arg-type]
        positions=positions,  # type: ignore[arg-type]
        account_snapshots=account_snapshots,  # type: ignore[arg-type]
        pnl_snapshots=pnl_snapshots,  # type: ignore[arg-type]
        strategy_start_at=None,
    )

    summary = await runner.run(summary_out=summary_out)

    assert run_sessions.records[0].status == "completed"
    assert run_sessions.records[0].summary_json == summary
    assert config_snapshots.items[0]["config_hash"] == "test-config-hash"
    assert len(instruments.items) == 1

    assert [item["signal_type"] for item in signal_events.items] == ["open_long", "close_long"]
    assert [item["decision"] for item in risk_decisions.items] == ["allow", "allow"]

    assert len(orders.history) == 4
    assert len(fills.items) == 2
    assert [fill.side for fill in fills.items] == ["buy", "sell"]

    assert len(positions.history) == 2
    assert positions.history[0].status == "open"
    assert positions.history[1].status == "closed"
    assert state_store.state.open_positions == {}

    assert len(account_snapshots.items) >= 3
    assert len(pnl_snapshots.items) >= 2
    assert account_snapshots.items[-1].equity == state_store.state.account_state.equity
    assert pnl_snapshots.items[-1].equity == state_store.state.account_state.equity
    assert summary["total_signals"] == 2
    assert summary["total_orders"] == 4
    assert summary["total_fills"] == 2
    assert float(summary["final_equity"]) < float(summary["initial_equity"])

    assert summary_out.exists()
    assert json.loads(summary_out.read_text(encoding="utf-8")) == summary
    assert any(key.endswith(":status") for key in redis.values)
    assert any(key.endswith(":snapshot:BTCUSDT") for key in redis.values)
