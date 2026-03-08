from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from trading_bot.config.schema import AppSettings
from trading_bot.domain.enums import ExchangeName, ExecutionVenueKind
from trading_bot.domain.models import ExecutionPlan, OrderIntent
from trading_bot.live.venue import LiveVenue
from trading_bot.observability.metrics import AppMetrics


def _build_settings() -> AppSettings:
    return AppSettings.model_validate(
        {
            "runtime": {"service_name": "tb", "mode": "live", "environment": "dev"},
            "exchange": {
                "primary": "binance",
                "market_type": "linear_perp",
                "position_mode": "one_way",
                "account_alias": "default",
                "testnet": True,
            },
            "symbols": {"allowlist": ["ETHUSDT"]},
            "storage": {"postgres_dsn": "postgresql+asyncpg://u:p@localhost/db", "redis_dsn": "redis://localhost:6379/0"},
            "observability": {"log_level": "INFO", "http_host": "127.0.0.1", "http_port": 8080},
            "live": {"execution_enabled": True, "allow_mainnet": True, "symbol_allowlist": ["ETHUSDT"]},
            "risk": {"max_open_positions": 1, "risk_per_trade": 0.01, "max_daily_loss": 0.2},
            "llm": {"enabled": False, "provider": "none", "model_name": "", "timeout_seconds": 10},
        }
    )


class _RestStub:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def create_order(self, **kwargs):
        self.calls.append(dict(kwargs))
        return {
            "orderId": 1234567890123456789,
            "clientOrderId": kwargs.get("client_order_id"),
        }


class _PrivateWsStub:
    async def stream(self, on_connection_state_change=None):  # pragma: no cover - not used in this test
        if on_connection_state_change is not None:
            await on_connection_state_change(False)
        if False:
            yield {}


async def test_live_submit_accepts_integer_exchange_order_id_from_rest() -> None:
    settings = _build_settings()
    rest = _RestStub()
    venue = LiveVenue(
        config=settings,
        metrics=AppMetrics(),
        rest_client=rest,
        private_ws_client=_PrivateWsStub(),
        private_message_normalizer=lambda _: [],
    )
    now = datetime.now(timezone.utc)
    plan = ExecutionPlan(
        execution_venue=ExecutionVenueKind.LIVE,
        intent_id="intent-1",
        entry_order=OrderIntent(
            intent_id="intent-1",
            exchange_name=ExchangeName.BINANCE,
            execution_venue=ExecutionVenueKind.LIVE,
            symbol="ETHUSDT",
            side="buy",
            order_type="market",
            quantity=Decimal("0.012"),
            submitted_at=now,
            metadata={"order_role": "entry"},
        ),
    )

    result = await venue.submit(plan)

    assert result.accepted is True
    assert len(result.orders) == 1
    assert result.orders[0].exchange_order_id == "1234567890123456789"
    assert result.orders[0].intent_id == "intent-1"


async def test_live_submit_binance_stop_market_uses_close_on_trigger() -> None:
    settings = _build_settings()
    rest = _RestStub()
    venue = LiveVenue(
        config=settings,
        metrics=AppMetrics(),
        rest_client=rest,
        private_ws_client=_PrivateWsStub(),
        private_message_normalizer=lambda _: [],
    )
    now = datetime.now(timezone.utc)
    plan = ExecutionPlan(
        execution_venue=ExecutionVenueKind.LIVE,
        intent_id="intent-2",
        entry_order=OrderIntent(
            intent_id="intent-2",
            exchange_name=ExchangeName.BINANCE,
            execution_venue=ExecutionVenueKind.LIVE,
            symbol="ETHUSDT",
            side="sell",
            order_type="stop_market",
            quantity=Decimal("0.012"),
            stop_price=Decimal("1983.6"),
            reduce_only=True,
            submitted_at=now,
            metadata={"order_role": "stop_loss"},
        ),
    )

    result = await venue.submit(plan)

    assert result.accepted is True
    assert len(rest.calls) == 1
    assert rest.calls[0]["close_on_trigger"] is True
