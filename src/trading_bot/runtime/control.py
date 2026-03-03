from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from trading_bot.config.schema import AppSettings
from trading_bot.domain.enums import TradeAction
from trading_bot.domain.models import BracketState, PositionState, TradeIntent
from trading_bot.runtime.state import RuntimeStateStore


@dataclass(frozen=True, slots=True)
class ControlCommandResponse:
    command: str
    outcome: str
    message: str


@dataclass(frozen=True, slots=True)
class FlattenRequest:
    requested_at: datetime
    source: str
    requested_by: int | None = None
    chat_id: int | None = None


@dataclass(frozen=True, slots=True)
class RuntimeStatusSnapshot:
    service_name: str
    run_mode: str
    environment: str
    run_session_id: str
    paused: bool
    flatten_pending: bool
    started_at: datetime | None
    last_market_event_at: datetime | None
    account_equity: Decimal | None
    available_balance: Decimal | None
    open_positions: tuple[PositionState, ...]
    open_orders_count: int
    active_brackets: tuple[BracketState, ...]
    last_kill_switch_reason: str | None
    protection_failure_active: bool
    cooldown_until: datetime | None
    last_command: str | None
    last_command_status: str | None
    last_command_detail: str | None
    last_command_at: datetime | None


@dataclass(frozen=True, slots=True)
class RuntimeRiskSnapshot:
    paused: bool
    max_open_positions: int
    open_positions: int
    risk_per_trade: float
    max_daily_loss: float
    leverage_cap: Decimal
    one_position_per_symbol: bool
    max_consecutive_losses: int
    cooldown_minutes_after_loss_streak: int
    funding_blackout_minutes_before: int
    funding_blackout_minutes_after: int
    current_equity: Decimal | None
    day_start_equity: Decimal | None
    current_drawdown_ratio: Decimal | None
    consecutive_losses: int
    cooldown_until: datetime | None
    daily_loss_breached_until: datetime | None
    protection_failure_active: bool
    protection_failure_reason: str | None
    last_kill_switch_reason: str | None


class RuntimeControlPlane:
    def __init__(self, *, config: AppSettings, state_store: RuntimeStateStore) -> None:
        self._config = config
        self._state_store = state_store
        self._paused = False
        self._pending_flatten: FlattenRequest | None = None
        self._started_at: datetime | None = None
        self._last_market_event_at: datetime | None = None
        self._last_command: str | None = None
        self._last_command_status: str | None = None
        self._last_command_detail: str | None = None
        self._last_command_at: datetime | None = None

    def bind_run(self, *, started_at: datetime) -> None:
        self._started_at = started_at

    def note_market_event(self, *, as_of: datetime) -> None:
        self._last_market_event_at = as_of

    @property
    def paused(self) -> bool:
        return self._paused

    def should_block_intent(self, intent: TradeIntent) -> bool:
        return self._paused and intent.action in {TradeAction.OPEN_LONG, TradeAction.OPEN_SHORT}

    def pause(
        self,
        *,
        source: str,
        requested_at: datetime,
        requested_by: int | None = None,
    ) -> ControlCommandResponse:
        if self._paused:
            self._record_command(
                command="pause",
                status="noop",
                detail="Entry generation is already paused.",
                requested_at=requested_at,
            )
            return ControlCommandResponse("pause", "noop", "Entry generation is already paused.")
        self._paused = True
        self._record_command(
            command="pause",
            status="accepted",
            detail="Entry generation paused. Existing positions remain managed.",
            requested_at=requested_at,
        )
        return ControlCommandResponse(
            "pause",
            "accepted",
            "Entry generation paused. Existing positions remain managed.",
        )

    def resume(
        self,
        *,
        source: str,
        requested_at: datetime,
        requested_by: int | None = None,
    ) -> ControlCommandResponse:
        if not self._paused:
            self._record_command(
                command="resume",
                status="noop",
                detail="Entry generation is already active.",
                requested_at=requested_at,
            )
            return ControlCommandResponse("resume", "noop", "Entry generation is already active.")
        self._paused = False
        self._record_command(
            command="resume",
            status="accepted",
            detail="Entry generation resumed.",
            requested_at=requested_at,
        )
        return ControlCommandResponse("resume", "accepted", "Entry generation resumed.")

    def request_flatten(
        self,
        *,
        source: str,
        requested_at: datetime,
        requested_by: int | None = None,
        chat_id: int | None = None,
    ) -> ControlCommandResponse:
        if not self._state_store.state.open_positions:
            self._record_command(
                command="flatten",
                status="noop",
                detail="No open positions to flatten.",
                requested_at=requested_at,
            )
            return ControlCommandResponse("flatten", "noop", "No open positions to flatten.")
        if self._pending_flatten is not None:
            self._record_command(
                command="flatten",
                status="noop",
                detail="Flatten request is already pending.",
                requested_at=requested_at,
            )
            return ControlCommandResponse("flatten", "noop", "Flatten request is already pending.")
        self._pending_flatten = FlattenRequest(
            requested_at=requested_at,
            source=source,
            requested_by=requested_by,
            chat_id=chat_id,
        )
        self._record_command(
            command="flatten",
            status="queued",
            detail=(
                f"Flatten queued for {len(self._state_store.state.open_positions)} "
                "open position(s)."
            ),
            requested_at=requested_at,
        )
        return ControlCommandResponse(
            "flatten",
            "queued",
            f"Flatten queued for {len(self._state_store.state.open_positions)} open position(s).",
        )

    def take_pending_flatten(self) -> FlattenRequest | None:
        request = self._pending_flatten
        self._pending_flatten = None
        return request

    def complete_flatten(self, *, requested_at: datetime, detail: str, success: bool) -> None:
        self._record_command(
            command="flatten",
            status="completed" if success else "failed",
            detail=detail,
            requested_at=requested_at,
        )

    def build_status_snapshot(self) -> RuntimeStatusSnapshot:
        state = self._state_store.state
        account = state.account_state
        kill_switch = state.kill_switch_state
        positions = tuple(sorted(state.open_positions.values(), key=lambda item: item.symbol))
        brackets = tuple(
            sorted(state.active_brackets_by_symbol.values(), key=lambda item: item.symbol)
        )
        return RuntimeStatusSnapshot(
            service_name=self._config.runtime.service_name,
            run_mode=self._config.runtime.mode.value,
            environment=self._config.runtime.environment.value,
            run_session_id=state.run_session_id,
            paused=self._paused,
            flatten_pending=self._pending_flatten is not None,
            started_at=self._started_at,
            last_market_event_at=self._last_market_event_at,
            account_equity=account.equity if account is not None else None,
            available_balance=account.available_balance if account is not None else None,
            open_positions=positions,
            open_orders_count=len(state.open_orders),
            active_brackets=brackets,
            last_kill_switch_reason=kill_switch.last_reason,
            protection_failure_active=kill_switch.protection_failure_active,
            cooldown_until=kill_switch.consecutive_loss_cooldown_until,
            last_command=self._last_command,
            last_command_status=self._last_command_status,
            last_command_detail=self._last_command_detail,
            last_command_at=self._last_command_at,
        )

    def build_risk_snapshot(self) -> RuntimeRiskSnapshot:
        state = self._state_store.state
        account = state.account_state
        day_key = self._current_day_key(account.updated_at if account is not None else None)
        day_start_equity = state.day_start_equity_by_utc_date.get(day_key)
        drawdown_ratio: Decimal | None = None
        if account is not None and day_start_equity is not None and day_start_equity > 0:
            drawdown_ratio = max(
                (day_start_equity - account.equity) / day_start_equity,
                Decimal("0"),
            )
        kill_switch = state.kill_switch_state
        loss_state = state.loss_streak_state
        return RuntimeRiskSnapshot(
            paused=self._paused,
            max_open_positions=self._config.risk.max_open_positions,
            open_positions=len(state.open_positions),
            risk_per_trade=self._config.risk.risk_per_trade,
            max_daily_loss=self._config.risk.max_daily_loss,
            leverage_cap=self._config.risk.leverage_cap,
            one_position_per_symbol=self._config.risk.one_position_per_symbol,
            max_consecutive_losses=self._config.risk.max_consecutive_losses,
            cooldown_minutes_after_loss_streak=self._config.risk.cooldown_minutes_after_loss_streak,
            funding_blackout_minutes_before=self._config.risk.funding_blackout_minutes_before,
            funding_blackout_minutes_after=self._config.risk.funding_blackout_minutes_after,
            current_equity=account.equity if account is not None else None,
            day_start_equity=day_start_equity,
            current_drawdown_ratio=drawdown_ratio,
            consecutive_losses=loss_state.consecutive_losses,
            cooldown_until=loss_state.cooldown_until,
            daily_loss_breached_until=kill_switch.daily_loss_breached_until,
            protection_failure_active=kill_switch.protection_failure_active,
            protection_failure_reason=kill_switch.protection_failure_reason,
            last_kill_switch_reason=kill_switch.last_reason,
        )

    def _current_day_key(self, reference: datetime | None) -> str:
        current = reference or datetime.now(UTC)
        return current.astimezone(UTC).date().isoformat()

    def _record_command(
        self,
        *,
        command: str,
        status: str,
        detail: str,
        requested_at: datetime,
    ) -> None:
        self._last_command = command
        self._last_command_status = status
        self._last_command_detail = detail
        self._last_command_at = requested_at
