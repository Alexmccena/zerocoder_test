from __future__ import annotations

from decimal import Decimal

import structlog

from trading_bot.alerts.service import TelegramAlertService, TelegramCommandHandler
from trading_bot.alerts.telegram import TelegramInboundMessage
from trading_bot.config.schema import AppSettings
from trading_bot.domain.enums import ExecutionVenueKind, RunMode
from trading_bot.domain.models import AccountState
from trading_bot.observability.metrics import AppMetrics
from trading_bot.runtime.control import RuntimeControlPlane
from trading_bot.runtime.state import RuntimeStateStore


class FakeTelegramClient:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def poll_messages(self, *, offset: int | None, timeout_seconds: int):
        return []

    async def send_message(self, *, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))

    async def close(self) -> None:
        return None


def _build_settings(min_severity: str = "info") -> AppSettings:
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
                    "chat_ids": [1001, 1002],
                    "allowed_chat_ids": [1001],
                    "allowed_user_ids": [2002],
                    "min_severity": min_severity,
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


def _build_control_plane(settings: AppSettings) -> RuntimeControlPlane:
    state_store = RuntimeStateStore(
        run_mode=RunMode.PAPER,
        execution_venue=ExecutionVenueKind.PAPER,
    )
    state_store.attach_run_session("run-123")
    state_store.set_account(
        AccountState(
            exchange_name="bybit",
            execution_venue=ExecutionVenueKind.PAPER,
            equity=Decimal("10000"),
            available_balance=Decimal("9900"),
        )
    )
    return RuntimeControlPlane(config=settings, state_store=state_store)


def test_telegram_command_handler_authorizes_and_formats_status() -> None:
    settings = _build_settings()
    control_plane = _build_control_plane(settings)
    handler = TelegramCommandHandler(
        config=settings,
        control_plane=control_plane,
        metrics=AppMetrics(),
    )

    authorized = handler.handle_message(
        TelegramInboundMessage(update_id=1, chat_id=1001, user_id=2002, text="/status@paperbot")
    )
    unauthorized = handler.handle_message(
        TelegramInboundMessage(update_id=2, chat_id=9999, user_id=2002, text="/status")
    )

    assert authorized is not None
    assert authorized.outcome == "handled"
    assert authorized.reply_text is not None
    assert "run_session_id: run-123" in authorized.reply_text
    assert unauthorized is not None
    assert unauthorized.outcome == "unauthorized"
    assert unauthorized.reply_text == "Unauthorized command sender."


async def test_telegram_alert_service_filters_by_severity() -> None:
    settings = _build_settings(min_severity="warning")
    client = FakeTelegramClient()
    service = TelegramAlertService(
        config=settings,
        token="token",
        logger=structlog.get_logger(),
        metrics=AppMetrics(),
        control_plane=_build_control_plane(settings),
        client=client,
    )

    await service.broadcast(kind="startup", severity="info", text="startup")
    await service.broadcast(kind="risk_halt", severity="critical", text="risk halt")

    assert client.sent == [(1001, "risk halt"), (1002, "risk halt")]
