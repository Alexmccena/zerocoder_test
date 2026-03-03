from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trading_bot.config.schema import AppSettings
from trading_bot.domain.enums import ExecutionVenueKind, RunMode, TradeAction
from trading_bot.domain.models import AccountState, BracketState, PositionState, TradeIntent
from trading_bot.runtime.control import RuntimeControlPlane
from trading_bot.runtime.state import RuntimeStateStore


def _build_settings() -> AppSettings:
    return AppSettings.model_validate(
        {
            "runtime": {
                "service_name": "trading-bot",
                "mode": "paper",
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
            "alerts": {
                "telegram": {
                    "enabled": True,
                    "chat_ids": [1001],
                    "allowed_chat_ids": [1001],
                    "allowed_user_ids": [2002],
                }
            },
            "risk": {
                "max_open_positions": 2,
                "risk_per_trade": 0.01,
                "max_daily_loss": 0.02,
            },
            "llm": {"enabled": False, "provider": "none", "model_name": "", "timeout_seconds": 10},
        }
    )


def _build_state_store() -> RuntimeStateStore:
    state = RuntimeStateStore(run_mode=RunMode.PAPER, execution_venue=ExecutionVenueKind.PAPER)
    state.attach_run_session("run-123")
    as_of = datetime(2026, 3, 4, 12, 0, tzinfo=UTC)
    state.set_account(
        AccountState(
            exchange_name="bybit",
            execution_venue=ExecutionVenueKind.PAPER,
            equity=Decimal("9800"),
            available_balance=Decimal("9700"),
            wallet_balance=Decimal("9800"),
            updated_at=as_of,
        )
    )
    state.state.day_start_equity_by_utc_date["2026-03-04"] = Decimal("10000")
    state.update_position(
        PositionState(
            exchange_name="bybit",
            execution_venue=ExecutionVenueKind.PAPER,
            symbol="BTCUSDT",
            side="long",
            quantity=Decimal("0.01"),
            entry_price=Decimal("90000"),
        )
    )
    state.state.active_brackets_by_symbol["BTCUSDT"] = BracketState(
        symbol="BTCUSDT",
        intent_id="intent-1",
        side="long",
        quantity=Decimal("0.01"),
        stop_loss_price=Decimal("89500"),
        take_profit_price=Decimal("91000"),
        status="armed",
    )
    return state


def test_runtime_control_plane_pauses_new_entries_and_tracks_flatten() -> None:
    state_store = _build_state_store()
    control = RuntimeControlPlane(config=_build_settings(), state_store=state_store)
    now = datetime(2026, 3, 4, 12, 5, tzinfo=UTC)
    control.bind_run(started_at=now)
    control.note_market_event(as_of=now)

    pause_result = control.pause(source="telegram", requested_at=now, requested_by=2002)
    open_intent = TradeIntent(
        strategy_name="test",
        action=TradeAction.OPEN_LONG,
        symbol="BTCUSDT",
        side="buy",
        reference_price=Decimal("90000"),
    )
    close_intent = TradeIntent(
        strategy_name="test",
        action=TradeAction.CLOSE_LONG,
        symbol="BTCUSDT",
        side="sell",
        reference_price=Decimal("90000"),
    )

    flatten_result = control.request_flatten(
        source="telegram",
        requested_at=now,
        requested_by=2002,
        chat_id=1001,
    )
    pending_request = control.take_pending_flatten()
    control.complete_flatten(
        requested_at=now,
        detail="Flatten processed for: BTCUSDT.",
        success=True,
    )

    assert pause_result.outcome == "accepted"
    assert control.should_block_intent(open_intent) is True
    assert control.should_block_intent(close_intent) is False
    assert flatten_result.outcome == "queued"
    assert pending_request is not None

    status = control.build_status_snapshot()
    assert status.paused is True
    assert status.flatten_pending is False
    assert status.last_command == "flatten"
    assert status.last_command_status == "completed"
    assert len(status.open_positions) == 1
    assert len(status.active_brackets) == 1


def test_runtime_control_plane_builds_risk_snapshot_with_drawdown() -> None:
    control = RuntimeControlPlane(config=_build_settings(), state_store=_build_state_store())

    snapshot = control.build_risk_snapshot()

    assert snapshot.open_positions == 1
    assert snapshot.current_equity == Decimal("9800")
    assert snapshot.day_start_equity == Decimal("10000")
    assert snapshot.current_drawdown_ratio == Decimal("0.02")
