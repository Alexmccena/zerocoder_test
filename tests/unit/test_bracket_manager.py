from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from trading_bot.config.schema import AppSettings
from trading_bot.domain.enums import ExchangeName, ExecutionVenueKind
from trading_bot.domain.models import (
    AccountState,
    ExecutionPlan,
    ExecutionResult,
    OrderIntent,
    OrderState,
    PositionState,
    VenueStateSnapshot,
)
from trading_bot.execution.bracket_manager import BracketManager


def _build_settings() -> AppSettings:
    return AppSettings.model_validate(
        {
            "runtime": {"service_name": "tb", "mode": "paper", "environment": "dev"},
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
            "risk": {"max_open_positions": 1, "risk_per_trade": 0.01, "max_daily_loss": 0.2},
            "llm": {"enabled": False, "provider": "none", "model_name": "", "timeout_seconds": 10},
        }
    )


class _FakeVenue:
    def __init__(self) -> None:
        self.submitted: list[ExecutionPlan] = []
        self._order_counter = 0

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def submit(self, plan: ExecutionPlan) -> ExecutionResult:
        self.submitted.append(plan)
        self._order_counter += 1
        intent = plan.entry_order
        order = OrderState(
            order_id=f"protective-{self._order_counter}",
            exchange_name=intent.exchange_name,
            execution_venue=intent.execution_venue,
            symbol=intent.symbol,
            side=intent.side,
            order_type=intent.order_type,
            status="working",
            quantity=intent.quantity,
            price=intent.price,
            stop_price=intent.stop_price,
            reduce_only=intent.reduce_only,
            intent_id=intent.intent_id,
            raw_payload={"order_role": intent.metadata.get("order_role")},
            submitted_at=intent.submitted_at,
            created_at=intent.submitted_at,
            updated_at=intent.submitted_at,
        )
        return ExecutionResult(accepted=True, orders=[order])

    async def process_market_event(self, symbol, snapshot, as_of):  # pragma: no cover - not used in this test
        return ExecutionResult(accepted=True)

    async def drain_pending_updates(self, *, as_of):  # pragma: no cover - not used in this test
        return ExecutionResult(accepted=True)

    async def cancel_order(self, order_id: str, *, as_of: datetime) -> ExecutionResult:
        return ExecutionResult(accepted=True)

    async def snapshot_state(self) -> VenueStateSnapshot:
        return VenueStateSnapshot()

    async def sync_positions(self) -> list[PositionState]:
        return []

    def account_state(self) -> AccountState:
        now = datetime.now(timezone.utc)
        return AccountState(
            exchange_name=ExchangeName.BINANCE,
            execution_venue=ExecutionVenueKind.LIVE,
            equity=Decimal("100"),
            available_balance=Decimal("100"),
            wallet_balance=Decimal("100"),
            margin_balance=Decimal("100"),
            updated_at=now,
        )


def _build_plan(*, as_of: datetime) -> ExecutionPlan:
    entry = OrderIntent(
        intent_id="intent-1",
        exchange_name=ExchangeName.BINANCE,
        execution_venue=ExecutionVenueKind.LIVE,
        symbol="ETHUSDT",
        side="buy",
        order_type="market",
        quantity=Decimal("0.010"),
        submitted_at=as_of,
        metadata={"order_role": "entry"},
    )
    stop = OrderIntent(
        intent_id="intent-1",
        exchange_name=ExchangeName.BINANCE,
        execution_venue=ExecutionVenueKind.LIVE,
        symbol="ETHUSDT",
        side="sell",
        order_type="stop_market",
        quantity=Decimal("0.010"),
        stop_price=Decimal("1990"),
        reduce_only=True,
        submitted_at=as_of,
        metadata={"order_role": "stop_loss"},
    )
    take = OrderIntent(
        intent_id="intent-1",
        exchange_name=ExchangeName.BINANCE,
        execution_venue=ExecutionVenueKind.LIVE,
        symbol="ETHUSDT",
        side="sell",
        order_type="limit",
        quantity=Decimal("0.010"),
        price=Decimal("2010"),
        reduce_only=True,
        submitted_at=as_of,
        metadata={"order_role": "take_profit"},
    )
    return ExecutionPlan(
        execution_venue=ExecutionVenueKind.LIVE,
        entry_order=entry,
        protective_orders=[stop, take],
        intent_id="intent-1",
    )


def _entry_order_state(*, as_of: datetime, status: str) -> OrderState:
    return OrderState(
        order_id="entry-1",
        exchange_name=ExchangeName.BINANCE,
        execution_venue=ExecutionVenueKind.LIVE,
        symbol="ETHUSDT",
        side="buy",
        order_type="market",
        status=status,
        quantity=Decimal("0.010"),
        intent_id="intent-1",
        submitted_at=as_of,
        created_at=as_of,
        updated_at=as_of,
    )


def test_bracket_manager_keeps_pending_bracket_until_position_opens() -> None:
    settings = _build_settings()
    venue = _FakeVenue()
    manager = BracketManager(config=settings, venue=venue)
    as_of = datetime.now(timezone.utc)

    manager.register_plan(
        plan=_build_plan(as_of=as_of),
        result=ExecutionResult(accepted=True, orders=[_entry_order_state(as_of=as_of, status="new")]),
    )

    closed_position = PositionState(
        exchange_name=ExchangeName.BINANCE,
        execution_venue=ExecutionVenueKind.LIVE,
        symbol="ETHUSDT",
        side="long",
        quantity=Decimal("0"),
        entry_price=Decimal("2000"),
        status="closed",
        updated_at=as_of,
    )
    __import__("asyncio").run(
        manager.on_execution_result(
            ExecutionResult(accepted=True, positions=[closed_position]),
            as_of=as_of,
        )
    )

    pending_bracket = manager.active_brackets().get("ETHUSDT")
    assert pending_bracket is not None
    assert pending_bracket.status == "pending_entry"
    assert len(venue.submitted) == 0

    __import__("asyncio").run(
        manager.on_execution_result(
            ExecutionResult(accepted=True, orders=[_entry_order_state(as_of=as_of, status="filled")]),
            as_of=as_of,
        )
    )
    assert len(venue.submitted) == 0

    open_position = PositionState(
        exchange_name=ExchangeName.BINANCE,
        execution_venue=ExecutionVenueKind.LIVE,
        symbol="ETHUSDT",
        side="long",
        quantity=Decimal("0.010"),
        entry_price=Decimal("2000"),
        status="open",
        updated_at=as_of,
    )
    arm_result = __import__("asyncio").run(
        manager.on_execution_result(
            ExecutionResult(accepted=True, positions=[open_position]),
            as_of=as_of,
        )
    )

    assert arm_result.accepted is True
    assert [plan.entry_order.metadata.get("order_role") for plan in venue.submitted] == ["stop_loss", "take_profit"]
    armed_bracket = manager.active_brackets()["ETHUSDT"]
    assert armed_bracket.status == "armed"
    assert armed_bracket.stop_loss_order_id is not None
    assert armed_bracket.take_profit_order_id is not None
