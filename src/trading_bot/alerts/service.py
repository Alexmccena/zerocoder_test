from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from structlog.stdlib import BoundLogger

from trading_bot.config.schema import AppSettings
from trading_bot.observability.metrics import AppMetrics
from trading_bot.runtime.control import (
    RuntimeControlPlane,
    RuntimeRiskSnapshot,
    RuntimeStatusSnapshot,
)

from .protocols import OperationalAlertSink
from .telegram import TelegramBotClient, TelegramInboundMessage


class AdvisoryCommandService(Protocol):
    async def operator_analyze(
        self,
        *,
        prompt: str,
        payload: dict[str, object],
        requested_at: datetime,
    ) -> str: ...

    async def playbook_set(self, *, text_or_json: str, source: str = "telegram") -> str: ...

    async def playbook_show(self) -> str: ...

    async def playbook_clear(self) -> str: ...


@dataclass(frozen=True, slots=True)
class CommandHandlingResult:
    command: str
    outcome: str
    reply_text: str | None
    broadcast_text: str | None = None
    broadcast_kind: str = "command"
    broadcast_severity: str = "info"


def _format_decimal(value: Decimal | None) -> str:
    return "n/a" if value is None else format(value.normalize(), "f")


def _format_datetime(value: datetime | None) -> str:
    return "n/a" if value is None else value.astimezone(UTC).isoformat()


def _command_broadcast_text(*, command: str, outcome: str, message: TelegramInboundMessage) -> str:
    return (
        f"Operator command /{command} {outcome}. "
        f"chat_id={message.chat_id} user_id={message.user_id}"
    )


def format_status_snapshot(snapshot: RuntimeStatusSnapshot) -> str:
    lines = [
        "Status",
        f"service: {snapshot.service_name}",
        f"mode: {snapshot.run_mode}",
        f"execution_venue: {snapshot.execution_venue}",
        f"environment: {snapshot.environment}",
        f"run_session_id: {snapshot.run_session_id or 'n/a'}",
        f"paused: {'yes' if snapshot.paused else 'no'}",
        f"flatten_pending: {'yes' if snapshot.flatten_pending else 'no'}",
        f"started_at: {_format_datetime(snapshot.started_at)}",
        f"last_market_event_at: {_format_datetime(snapshot.last_market_event_at)}",
        f"equity: {_format_decimal(snapshot.account_equity)}",
        f"available_balance: {_format_decimal(snapshot.available_balance)}",
        f"open_orders: {snapshot.open_orders_count}",
        f"open_positions: {len(snapshot.open_positions)}",
    ]
    if snapshot.execution_venue == "live":
        lines.extend(
            [
                f"network: {snapshot.venue_network}",
                f"execution_enabled: {'yes' if snapshot.live_execution_enabled else 'no'}",
                f"allow_mainnet: {'yes' if snapshot.live_allow_mainnet else 'no'}",
                f"live_symbol_allowlist: {','.join(snapshot.live_symbol_allowlist) or 'n/a'}",
                f"private_ws_connected: {'yes' if snapshot.private_ws_connected else 'no'}",
                f"last_private_event_at: {_format_datetime(snapshot.last_private_event_at)}",
                f"last_successful_rest_sync_at: {_format_datetime(snapshot.last_successful_rest_sync_at)}",
                f"live_total_exposure_usdt: {_format_decimal(snapshot.live_total_exposure_usdt)}",
            ]
        )
    if snapshot.open_positions:
        position_items = ", ".join(
            f"{position.symbol}:{position.side}:{_format_decimal(position.quantity)}"
            for position in snapshot.open_positions
        )
        lines.append(f"positions: {position_items}")
    if snapshot.active_brackets:
        bracket_items = ", ".join(
            f"{bracket.symbol}:{bracket.status}"
            for bracket in snapshot.active_brackets
        )
        lines.append(f"brackets: {bracket_items}")
    lines.extend(
        [
            f"kill_switch: {snapshot.last_kill_switch_reason or 'none'}",
            f"protection_failure: {'yes' if snapshot.protection_failure_active else 'no'}",
            f"cooldown_until: {_format_datetime(snapshot.cooldown_until)}",
            f"last_command: {snapshot.last_command or 'none'}",
            f"last_command_status: {snapshot.last_command_status or 'n/a'}",
            f"last_command_at: {_format_datetime(snapshot.last_command_at)}",
        ]
    )
    if snapshot.last_command_detail is not None:
        lines.append(f"last_command_detail: {snapshot.last_command_detail}")
    return "\n".join(lines)


def format_risk_snapshot(snapshot: RuntimeRiskSnapshot) -> str:
    lines = [
        "Risk",
        f"paused: {'yes' if snapshot.paused else 'no'}",
        f"risk_per_trade: {snapshot.risk_per_trade}",
        f"max_daily_loss: {snapshot.max_daily_loss}",
        f"leverage_cap: {_format_decimal(snapshot.leverage_cap)}",
        f"open_positions: {snapshot.open_positions}/{snapshot.max_open_positions}",
        f"one_position_per_symbol: {'yes' if snapshot.one_position_per_symbol else 'no'}",
        (
            "loss_streak: "
            f"{snapshot.consecutive_losses}/{snapshot.max_consecutive_losses}"
        ),
        f"cooldown_after_loss_minutes: {snapshot.cooldown_minutes_after_loss_streak}",
        f"cooldown_until: {_format_datetime(snapshot.cooldown_until)}",
        (
            "funding_blackout_minutes: "
            f"{snapshot.funding_blackout_minutes_before} before / "
            f"{snapshot.funding_blackout_minutes_after} after"
        ),
        f"current_equity: {_format_decimal(snapshot.current_equity)}",
        f"day_start_equity: {_format_decimal(snapshot.day_start_equity)}",
        f"drawdown_ratio: {_format_decimal(snapshot.current_drawdown_ratio)}",
        f"daily_loss_breached_until: {_format_datetime(snapshot.daily_loss_breached_until)}",
        f"last_kill_switch_reason: {snapshot.last_kill_switch_reason or 'none'}",
        f"protection_failure: {'yes' if snapshot.protection_failure_active else 'no'}",
    ]
    if snapshot.live_execution_enabled is not None:
        lines.extend(
            [
                f"live_execution_enabled: {'yes' if snapshot.live_execution_enabled else 'no'}",
                f"live_allow_mainnet: {'yes' if snapshot.live_allow_mainnet else 'no'}",
                f"live_max_order_notional_usdt: {_format_decimal(snapshot.live_max_order_notional_usdt)}",
                f"live_max_position_notional_usdt: {_format_decimal(snapshot.live_max_position_notional_usdt)}",
                f"live_max_total_exposure_usdt: {_format_decimal(snapshot.live_max_total_exposure_usdt)}",
                (
                    "live_private_state_stale_after_seconds: "
                    f"{snapshot.live_private_state_stale_after_seconds if snapshot.live_private_state_stale_after_seconds is not None else 'n/a'}"
                ),
                f"live_active_stale_reason: {snapshot.live_active_stale_reason or 'none'}",
            ]
        )
    if snapshot.protection_failure_reason is not None:
        lines.append(f"protection_failure_reason: {snapshot.protection_failure_reason}")
    return "\n".join(lines)


class TelegramCommandHandler:
    def __init__(
        self,
        *,
        config: AppSettings,
        control_plane: RuntimeControlPlane,
        metrics: AppMetrics,
        llm_service: AdvisoryCommandService | None = None,
    ) -> None:
        self._control_plane = control_plane
        self._metrics = metrics
        self._telegram = config.alerts.telegram
        self._llm_service = llm_service

    async def handle_message(self, message: TelegramInboundMessage) -> CommandHandlingResult | None:
        parsed = self._parse_command_and_args(message.text)
        if parsed is None:
            return None
        command, args = parsed
        if not self._is_authorized(chat_id=message.chat_id, user_id=message.user_id):
            self._metrics.record_telegram_command(command=command, outcome="unauthorized")
            return CommandHandlingResult(
                command=command,
                outcome="unauthorized",
                reply_text="Unauthorized command sender.",
                broadcast_text=None,
            )

        requested_at = datetime.now(UTC)
        if command == "status":
            self._metrics.record_telegram_command(command=command, outcome="handled")
            return CommandHandlingResult(
                command=command,
                outcome="handled",
                reply_text=format_status_snapshot(self._control_plane.build_status_snapshot()),
                broadcast_text=None,
            )
        if command == "risk":
            self._metrics.record_telegram_command(command=command, outcome="handled")
            return CommandHandlingResult(
                command=command,
                outcome="handled",
                reply_text=format_risk_snapshot(self._control_plane.build_risk_snapshot()),
                broadcast_text=None,
            )
        if command == "pause":
            reply = self._control_plane.pause(
                source="telegram",
                requested_at=requested_at,
                requested_by=message.user_id,
            )
            self._metrics.record_telegram_command(command=command, outcome=reply.outcome)
            return CommandHandlingResult(
                command=command,
                outcome=reply.outcome,
                reply_text=reply.message,
                broadcast_text=_command_broadcast_text(
                    command="pause",
                    outcome=reply.outcome,
                    message=message,
                ),
            )
        if command == "resume":
            reply = self._control_plane.resume(
                source="telegram",
                requested_at=requested_at,
                requested_by=message.user_id,
            )
            self._metrics.record_telegram_command(command=command, outcome=reply.outcome)
            return CommandHandlingResult(
                command=command,
                outcome=reply.outcome,
                reply_text=reply.message,
                broadcast_text=_command_broadcast_text(
                    command="resume",
                    outcome=reply.outcome,
                    message=message,
                ),
            )
        if command == "flatten":
            reply = self._control_plane.request_flatten(
                source="telegram",
                requested_at=requested_at,
                requested_by=message.user_id,
                chat_id=message.chat_id,
            )
            self._metrics.record_telegram_command(command=command, outcome=reply.outcome)
            return CommandHandlingResult(
                command=command,
                outcome=reply.outcome,
                reply_text=reply.message,
                broadcast_text=_command_broadcast_text(
                    command="flatten",
                    outcome=reply.outcome,
                    message=message,
                ),
                broadcast_severity="warning",
            )
        if command == "analyze":
            if self._llm_service is None:
                self._metrics.record_telegram_command(command=command, outcome="disabled")
                return CommandHandlingResult(
                    command=command,
                    outcome="disabled",
                    reply_text="LLM advisory is disabled.",
                    broadcast_text=None,
                )
            prompt = args.strip()
            if not prompt:
                self._metrics.record_telegram_command(command=command, outcome="invalid")
                return CommandHandlingResult(
                    command=command,
                    outcome="invalid",
                    reply_text="Usage: /analyze <prompt>",
                    broadcast_text=None,
                )
            analysis = await self._llm_service.operator_analyze(
                prompt=prompt,
                payload={
                    "status_snapshot": format_status_snapshot(self._control_plane.build_status_snapshot()),
                    "risk_snapshot": format_risk_snapshot(self._control_plane.build_risk_snapshot()),
                },
                requested_at=requested_at,
            )
            self._metrics.record_telegram_command(command=command, outcome="handled")
            return CommandHandlingResult(
                command=command,
                outcome="handled",
                reply_text=analysis,
                broadcast_text=None,
            )
        if command == "playbook":
            if self._llm_service is None:
                self._metrics.record_telegram_command(command=command, outcome="disabled")
                return CommandHandlingResult(
                    command=command,
                    outcome="disabled",
                    reply_text="LLM advisory is disabled.",
                    broadcast_text=None,
                )
            normalized_args = args.strip()
            if normalized_args.startswith("set "):
                payload = normalized_args[4:].strip()
                reply = await self._llm_service.playbook_set(text_or_json=payload, source="telegram")
                self._metrics.record_telegram_command(command="playbook_set", outcome="handled")
                return CommandHandlingResult(
                    command=command,
                    outcome="handled",
                    reply_text=reply,
                    broadcast_text=_command_broadcast_text(command="playbook_set", outcome="handled", message=message),
                )
            if normalized_args == "show":
                reply = await self._llm_service.playbook_show()
                self._metrics.record_telegram_command(command="playbook_show", outcome="handled")
                return CommandHandlingResult(
                    command=command,
                    outcome="handled",
                    reply_text=reply,
                    broadcast_text=None,
                )
            if normalized_args == "clear":
                reply = await self._llm_service.playbook_clear()
                self._metrics.record_telegram_command(command="playbook_clear", outcome="handled")
                return CommandHandlingResult(
                    command=command,
                    outcome="handled",
                    reply_text=reply,
                    broadcast_text=_command_broadcast_text(command="playbook_clear", outcome="handled", message=message),
                )
            self._metrics.record_telegram_command(command=command, outcome="invalid")
            return CommandHandlingResult(
                command=command,
                outcome="invalid",
                reply_text="Usage: /playbook set <text|json> | /playbook show | /playbook clear",
                broadcast_text=None,
            )

        self._metrics.record_telegram_command(command=command, outcome="unknown")
        return CommandHandlingResult(
            command=command,
            outcome="unknown",
            reply_text=(
                "Unknown command. Supported: /status /risk /pause /resume /flatten "
                "/analyze /playbook"
            ),
            broadcast_text=None,
        )

    def _is_authorized(self, *, chat_id: int, user_id: int | None) -> bool:
        allowed_chat_ids = set(self._telegram.allowed_chat_ids)
        allowed_user_ids = set(self._telegram.allowed_user_ids)
        chat_allowed = not allowed_chat_ids or chat_id in allowed_chat_ids
        user_allowed = not allowed_user_ids or user_id in allowed_user_ids
        return chat_allowed and user_allowed

    def _parse_command_and_args(self, text: str) -> tuple[str, str] | None:
        stripped = text.strip()
        if not stripped.startswith("/"):
            return None
        without_slash = stripped[1:]
        head, _, tail = without_slash.partition(" ")
        command = head.split("@", maxsplit=1)[0].lower()
        if not command:
            return None
        return command, tail.strip()


class TelegramAlertService(OperationalAlertSink):
    def __init__(
        self,
        *,
        config: AppSettings,
        token: str,
        logger: BoundLogger,
        metrics: AppMetrics,
        control_plane: RuntimeControlPlane,
        llm_service: AdvisoryCommandService | None = None,
        client: TelegramBotClient | None = None,
    ) -> None:
        self._telegram = config.alerts.telegram
        self._logger = logger
        self._metrics = metrics
        self._client = client or TelegramBotClient(
            token=token,
            timeout_seconds=self._telegram.long_poll_timeout_seconds + 5,
        )
        self._handler = TelegramCommandHandler(
            config=config,
            control_plane=control_plane,
            metrics=metrics,
            llm_service=llm_service,
        )
        self._offset: int | None = None

    async def run(self) -> None:
        while True:
            try:
                messages = await self._client.poll_messages(
                    offset=self._offset,
                    timeout_seconds=self._telegram.long_poll_timeout_seconds,
                )
                for message in messages:
                    self._offset = message.update_id + 1
                    await self._handle_message(message)
                if not messages:
                    await asyncio.sleep(self._telegram.poll_interval_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._logger.warning("telegram_poll_failed", error=str(exc))
                await asyncio.sleep(self._telegram.poll_interval_seconds)

    async def broadcast(self, *, kind: str, severity: str, text: str) -> None:
        if not self._should_broadcast(kind=kind, severity=severity):
            return
        for chat_id in dict.fromkeys(self._telegram.chat_ids):
            await self._safe_send(chat_id=chat_id, text=text, kind=kind, severity=severity)

    async def close(self) -> None:
        await self._client.close()

    async def _handle_message(self, message: TelegramInboundMessage) -> None:
        result = await self._handler.handle_message(message)
        if result is None:
            return
        if result.reply_text is not None:
            await self._safe_send(
                chat_id=message.chat_id,
                text=result.reply_text,
                kind="command_reply",
                severity="info",
            )
        if result.broadcast_text is not None:
            await self.broadcast(
                kind=result.broadcast_kind,
                severity=result.broadcast_severity,
                text=result.broadcast_text,
            )

    async def _safe_send(self, *, chat_id: int, text: str, kind: str, severity: str) -> None:
        try:
            await self._client.send_message(chat_id=chat_id, text=text)
        except Exception as exc:
            self._metrics.record_operational_alert(
                channel="telegram",
                severity=severity,
                kind=kind,
                success=False,
            )
            self._logger.warning(
                "telegram_send_failed",
                chat_id=chat_id,
                kind=kind,
                severity=severity,
                error=str(exc),
            )
        else:
            self._metrics.record_operational_alert(
                channel="telegram",
                severity=severity,
                kind=kind,
                success=True,
            )

    def _should_broadcast(self, *, kind: str, severity: str) -> bool:
        if self._severity_rank(severity) < self._severity_rank(self._telegram.min_severity):
            return False
        if kind == "startup":
            return self._telegram.startup_enabled
        if kind == "shutdown":
            return self._telegram.shutdown_enabled
        if kind == "command":
            return self._telegram.command_echo_enabled
        if kind == "risk_halt":
            return self._telegram.risk_halt_enabled
        if kind == "protection_failure":
            return self._telegram.protection_failure_enabled
        return True

    def _severity_rank(self, value: str) -> int:
        order = {"info": 0, "warning": 1, "critical": 2}
        return order[value]
