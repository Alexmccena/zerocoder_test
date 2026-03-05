from __future__ import annotations

from decimal import Decimal

import pytest
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


class FakeLLMService:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.playbook_text: str | None = None

    async def operator_analyze(self, *, prompt: str, payload: dict[str, object], requested_at) -> str:
        self.prompts.append(prompt)
        return f"Analyze\nprompt: {prompt}"

    async def playbook_set(self, *, text_or_json: str, source: str = "telegram") -> str:
        self.playbook_text = text_or_json
        return "Playbook updated."

    async def playbook_show(self) -> str:
        return "Playbook is not set." if self.playbook_text is None else f"Playbook\ntext: {self.playbook_text}"

    async def playbook_clear(self) -> str:
        self.playbook_text = None
        return "Playbook cleared."


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


@pytest.mark.asyncio
async def test_telegram_command_handler_authorizes_and_formats_status() -> None:
    settings = _build_settings()
    control_plane = _build_control_plane(settings)
    handler = TelegramCommandHandler(
        config=settings,
        control_plane=control_plane,
        metrics=AppMetrics(),
    )

    authorized = await handler.handle_message(
        TelegramInboundMessage(update_id=1, chat_id=1001, user_id=2002, text="/status@paperbot")
    )
    unauthorized = await handler.handle_message(
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


@pytest.mark.asyncio
async def test_telegram_command_handler_supports_analyze_and_playbook() -> None:
    settings = _build_settings()
    control_plane = _build_control_plane(settings)
    llm = FakeLLMService()
    handler = TelegramCommandHandler(
        config=settings,
        control_plane=control_plane,
        metrics=AppMetrics(),
        llm_service=llm,
    )

    analyze = await handler.handle_message(
        TelegramInboundMessage(update_id=3, chat_id=1001, user_id=2002, text="/analyze BTC momentum?")
    )
    playbook_set = await handler.handle_message(
        TelegramInboundMessage(update_id=4, chat_id=1001, user_id=2002, text="/playbook set avoid chop")
    )
    playbook_show = await handler.handle_message(
        TelegramInboundMessage(update_id=5, chat_id=1001, user_id=2002, text="/playbook show")
    )
    playbook_clear = await handler.handle_message(
        TelegramInboundMessage(update_id=6, chat_id=1001, user_id=2002, text="/playbook clear")
    )

    assert analyze is not None
    assert "prompt: BTC momentum?" in (analyze.reply_text or "")
    assert llm.prompts == ["BTC momentum?"]
    assert playbook_set is not None
    assert playbook_set.reply_text == "Playbook updated."
    assert playbook_show is not None
    assert "avoid chop" in (playbook_show.reply_text or "")
    assert playbook_clear is not None
    assert playbook_clear.reply_text == "Playbook cleared."
