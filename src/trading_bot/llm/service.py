from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import ValidationError
from structlog.stdlib import BoundLogger

from trading_bot.alerts.protocols import OperationalAlertSink
from trading_bot.config.schema import LLMConfig
from trading_bot.domain.models import AdvisorOutput
from trading_bot.llm.budget_guard import LLMBudgetGuard
from trading_bot.llm.contracts import AdvisorContext, PlaybookState, ProviderCompletion
from trading_bot.llm.hashing import stable_input_hash
from trading_bot.llm.prompts import render_system_prompt, render_user_prompt
from trading_bot.observability.metrics import AppMetrics


class LLMAdviceStore(Protocol):
    async def create_advice(
        self,
        *,
        run_session_id: str | None,
        symbol: str | None,
        advice_type: str,
        model_name: str,
        input_hash: str,
        output_json: dict[str, Any],
    ) -> Any: ...

    async def get_latest_playbook(self, run_session_id: str | None) -> Any: ...


class LLMProvider(Protocol):
    async def complete_json(
        self,
        *,
        model_name: str,
        system_prompt: str,
        user_prompt: str,
        timeout_seconds: int,
    ) -> ProviderCompletion: ...

    async def close(self) -> None: ...


@dataclass(slots=True)
class LLMAdvisoryService:
    config: LLMConfig
    logger: BoundLogger
    metrics: AppMetrics
    repository: LLMAdviceStore | None
    provider: LLMProvider | None
    alert_sink: OperationalAlertSink | None = None
    queue_maxsize: int = 256
    _queue: asyncio.Queue[AdvisorContext | None] = field(init=False, repr=False)
    _worker_task: asyncio.Task[None] | None = field(init=False, default=None, repr=False)
    _run_session_id: str | None = field(init=False, default=None)
    _playbook: PlaybookState | None = field(init=False, default=None)
    _budget_guard: LLMBudgetGuard = field(init=False, repr=False)
    _exec_lock: asyncio.Lock = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._queue = asyncio.Queue(maxsize=self.queue_maxsize)
        self._budget_guard = LLMBudgetGuard(self.config.budgets)
        self._exec_lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self.config.enabled and self.provider is not None

    @property
    def run_session_id(self) -> str | None:
        return self._run_session_id

    def set_alert_sink(self, sink: OperationalAlertSink | None) -> None:
        self.alert_sink = sink

    async def start(self, *, run_session_id: str) -> None:
        self._run_session_id = run_session_id
        if self.repository is not None:
            record = await self.repository.get_latest_playbook(run_session_id)
            self._playbook = self._playbook_from_record(record)
        if not self.enabled:
            return
        if self._worker_task is None:
            self._worker_task = asyncio.create_task(self._worker_loop(), name="llm-advisory-worker")

    async def stop(self) -> None:
        if self._worker_task is not None:
            await self._queue.put(None)
            await self._worker_task
            self._worker_task = None
        if self.provider is not None:
            await self.provider.close()

    def enqueue_workflow(
        self,
        *,
        workflow: str,
        payload: dict[str, Any],
        symbol: str | None = None,
        operator_prompt: str | None = None,
        requested_at: datetime | None = None,
    ) -> bool:
        if not self.enabled or self._run_session_id is None:
            return False
        if not self._workflow_enabled(workflow):
            return False
        context = AdvisorContext(
            workflow=workflow,  # type: ignore[arg-type]
            run_session_id=self._run_session_id,
            symbol=symbol,
            requested_at=requested_at or datetime.now(UTC),
            language=self.config.default_language,
            payload=payload,
            operator_prompt=operator_prompt,
            playbook=self._playbook,
        )
        try:
            self._queue.put_nowait(context)
            return True
        except asyncio.QueueFull:
            self.logger.warning("llm_queue_full", workflow=workflow, symbol=symbol)
            return False

    async def operator_analyze(
        self,
        *,
        prompt: str,
        payload: dict[str, Any],
        requested_at: datetime,
    ) -> str:
        if not self.enabled:
            return "LLM advisory disabled in current config."
        if self._run_session_id is None:
            return "LLM advisory is not initialized yet."
        context = AdvisorContext(
            workflow="operator_analyze",
            run_session_id=self._run_session_id,
            requested_at=requested_at,
            language=self.config.default_language,
            payload=payload,
            operator_prompt=prompt,
            playbook=self._playbook,
        )
        result = await self._execute_context(context)
        if result is None:
            return "LLM analyze failed or skipped (see logs/metrics)."
        return self._format_operator_reply(result)

    async def playbook_set(self, *, text_or_json: str, source: str = "telegram") -> str:
        now = datetime.now(UTC)
        stripped = text_or_json.strip()
        if not stripped:
            return "Playbook text is empty."
        constraints_json: dict[str, Any] = {}
        if stripped.startswith("{"):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, dict):
                    constraints_json = parsed
            except json.JSONDecodeError:
                constraints_json = {}
        self._playbook = PlaybookState(
            text=stripped,
            constraints_json=constraints_json,
            source="telegram" if source != "runtime" else "runtime",
            updated_at=now,
            run_session_id=self._run_session_id,
        )
        await self._persist_playbook_event(advice_type="playbook_set", payload=self._playbook.model_dump(mode="json"))
        return "Playbook updated."

    async def playbook_show(self) -> str:
        if self._playbook is None:
            return "Playbook is not set."
        lines = [
            "Playbook",
            f"updated_at: {self._playbook.updated_at.isoformat()}",
            f"source: {self._playbook.source}",
            f"text: {self._playbook.text}",
        ]
        if self._playbook.constraints_json:
            lines.append(f"constraints: {json.dumps(self._playbook.constraints_json, ensure_ascii=False)}")
        return "\n".join(lines)

    async def playbook_clear(self) -> str:
        now = datetime.now(UTC)
        self._playbook = None
        await self._persist_playbook_event(
            advice_type="playbook_clear",
            payload={"cleared": True, "updated_at": now.isoformat()},
        )
        return "Playbook cleared."

    async def _worker_loop(self) -> None:
        while True:
            context = await self._queue.get()
            if context is None:
                self._queue.task_done()
                break
            try:
                await self._execute_context(context)
            except Exception as exc:  # pragma: no cover - defensive guard
                self.logger.warning("llm_worker_unhandled_error", error=str(exc), workflow=context.workflow)
            finally:
                self._queue.task_done()

    async def _execute_context(self, context: AdvisorContext) -> AdvisorOutput | None:
        if not self.enabled or self.provider is None:
            return None
        model_name = self.config.active_analyst_model
        async with self._exec_lock:
            budget_decision = self._budget_guard.allow(now=context.requested_at)
            if not budget_decision.allowed:
                self.metrics.record_llm_budget_block(reason=budget_decision.reason or "unknown")
                await self._emit_budget_block_alert(reason=budget_decision.reason or "unknown")
                return None
            self._budget_guard.register_call(now=context.requested_at)
            system_prompt = render_system_prompt(workflow=context.workflow, language=context.language)
            user_prompt = render_user_prompt(context)
            input_hash = stable_input_hash(
                {
                    "workflow": context.workflow,
                    "symbol": context.symbol,
                    "operator_prompt": context.operator_prompt,
                    "playbook": context.playbook.model_dump(mode="json") if context.playbook is not None else None,
                    "payload": context.payload,
                }
            )
            try:
                completion = await self.provider.complete_json(
                    model_name=model_name,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    timeout_seconds=self.config.timeout_seconds,
                )
            except Exception as exc:
                self.metrics.record_llm_request(
                    provider=self.config.provider,
                    model=model_name,
                    status="error",
                    workflow=context.workflow,
                    latency_seconds=0.0,
                )
                await self._persist_advice(
                    context=context,
                    model_name=model_name,
                    input_hash=input_hash,
                    output_json={"error": str(exc), "workflow": context.workflow},
                    advice_type=context.workflow,
                )
                self.logger.warning(
                    "llm_request_failed",
                    workflow=context.workflow,
                    model=model_name,
                    error=str(exc),
                )
                return None
            self._budget_guard.register_usage(
                now=context.requested_at,
                input_tokens=completion.usage.input_tokens,
                output_tokens=completion.usage.output_tokens,
                cost_usd=completion.usage.cost_usd,
            )
            self.metrics.record_llm_request(
                provider=completion.provider,
                model=completion.model_name,
                status="ok",
                workflow=context.workflow,
                latency_seconds=completion.latency_seconds,
            )
            self.metrics.record_llm_tokens(
                provider=completion.provider,
                model=completion.model_name,
                input_tokens=completion.usage.input_tokens,
                output_tokens=completion.usage.output_tokens,
            )
            self.metrics.record_llm_cost(
                provider=completion.provider,
                model=completion.model_name,
                cost_usd=completion.usage.cost_usd,
            )
            try:
                output = AdvisorOutput.model_validate(completion.output_json)
            except ValidationError as exc:
                self.metrics.record_llm_parse_fail(workflow=context.workflow)
                await self._persist_advice(
                    context=context,
                    model_name=completion.model_name,
                    input_hash=input_hash,
                    output_json={
                        "parse_error": str(exc),
                        "raw_text": completion.raw_text,
                        "raw_json": completion.output_json,
                        "usage": completion.usage.model_dump(mode="json"),
                    },
                    advice_type=context.workflow,
                )
                self.logger.warning(
                    "llm_parse_failed",
                    workflow=context.workflow,
                    model=completion.model_name,
                    error=str(exc),
                )
                return None
            await self._persist_advice(
                context=context,
                model_name=completion.model_name,
                input_hash=input_hash,
                output_json={
                    "advisor_output": output.model_dump(mode="json"),
                    "usage": completion.usage.model_dump(mode="json"),
                    "workflow": context.workflow,
                },
                advice_type=context.workflow,
            )
            self.logger.info(
                "llm_advisory_created",
                workflow=context.workflow,
                model=completion.model_name,
                symbol=context.symbol,
                latency=completion.latency_seconds,
                input_tokens=completion.usage.input_tokens,
                output_tokens=completion.usage.output_tokens,
                cost_usd=completion.usage.cost_usd,
            )
            await self._emit_advice_alert(context=context, completion=completion, output=output)
            return output

    async def _persist_advice(
        self,
        *,
        context: AdvisorContext,
        model_name: str,
        input_hash: str,
        output_json: dict[str, Any],
        advice_type: str,
    ) -> None:
        if self.repository is None:
            return
        await self.repository.create_advice(
            run_session_id=context.run_session_id,
            symbol=context.symbol,
            advice_type=advice_type,
            model_name=model_name,
            input_hash=input_hash,
            output_json=output_json,
        )

    async def _persist_playbook_event(self, *, advice_type: str, payload: dict[str, Any]) -> None:
        if self.repository is None:
            return
        run_session_id = self._run_session_id
        await self.repository.create_advice(
            run_session_id=run_session_id,
            symbol=None,
            advice_type=advice_type,
            model_name="operator",
            input_hash=stable_input_hash({"advice_type": advice_type, "payload": payload}),
            output_json=payload,
        )

    def _playbook_from_record(self, record: Any) -> PlaybookState | None:
        if record is None:
            return None
        advice_type = getattr(record, "advice_type", None)
        if advice_type == "playbook_clear":
            return None
        if advice_type != "playbook_set":
            return None
        output_json = getattr(record, "output_json", None)
        if not isinstance(output_json, dict):
            return None
        try:
            return PlaybookState.model_validate(output_json)
        except ValidationError:
            return None

    def _workflow_enabled(self, workflow: str) -> bool:
        toggles = self.config.workflows
        if workflow == "pre_session":
            return toggles.pre_session_enabled
        if workflow == "periodic":
            return toggles.periodic_enabled
        if workflow == "post_trade":
            return toggles.post_trade_enabled
        if workflow == "risk_halt":
            return toggles.risk_halt_enabled
        return True

    async def _emit_advice_alert(
        self,
        *,
        context: AdvisorContext,
        completion: ProviderCompletion,
        output: AdvisorOutput,
    ) -> None:
        if self.alert_sink is None:
            return
        if context.workflow == "operator_analyze":
            return
        severity = "warning" if context.workflow == "risk_halt" else "info"
        text = (
            f"LLM {context.workflow} advice ({completion.model_name})\n"
            f"action: {output.action} confidence_pct: {output.confidence_pct:.1f}\n"
            f"market_regime: {output.market_regime}\n"
            f"narrative: {output.narrative}"
        )
        await self.alert_sink.broadcast(kind="llm_advice", severity=severity, text=text)

    async def _emit_budget_block_alert(self, *, reason: str) -> None:
        if self.alert_sink is None:
            return
        await self.alert_sink.broadcast(
            kind="llm_budget_block",
            severity="warning",
            text=f"LLM advisory skipped due to budget cap: {reason}",
        )

    def _format_operator_reply(self, output: AdvisorOutput) -> str:
        lines = [
            "Analyze",
            f"action: {output.action}",
            f"confidence_pct: {output.confidence_pct:.1f}",
            f"market_regime: {output.market_regime}",
            f"smart_money_signal: {output.smart_money_signal}",
            f"market_bias: {output.market_bias}",
            f"setup_quality_score: {output.setup_quality_score:.3f}",
        ]
        if output.recommended_focus_symbols:
            lines.append(f"focus_symbols: {','.join(output.recommended_focus_symbols)}")
        if output.warnings:
            lines.append(f"warnings: {' | '.join(output.warnings)}")
        lines.append(f"narrative: {output.narrative}")
        return "\n".join(lines)
