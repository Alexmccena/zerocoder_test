from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
import structlog

from trading_bot.config.schema import LLMConfig
from trading_bot.llm.contracts import ProviderCompletion, ProviderUsage
from trading_bot.llm.service import LLMAdvisoryService
from trading_bot.observability.metrics import AppMetrics


@dataclass
class InMemoryAdviceRepo:
    items: list[dict[str, Any]] = field(default_factory=list)

    async def create_advice(
        self,
        *,
        run_session_id: str | None,
        symbol: str | None,
        advice_type: str,
        model_name: str,
        input_hash: str,
        output_json: dict[str, Any],
    ) -> SimpleNamespace:
        item = {
            "run_session_id": run_session_id,
            "symbol": symbol,
            "advice_type": advice_type,
            "model_name": model_name,
            "input_hash": input_hash,
            "output_json": output_json,
        }
        self.items.append(item)
        return SimpleNamespace(**item)

    async def get_latest_playbook(self, run_session_id: str | None) -> Any:
        for item in reversed(self.items):
            if item["advice_type"] in {"playbook_set", "playbook_clear"} and item["run_session_id"] == run_session_id:
                return SimpleNamespace(**item)
        return None


class FakeProvider:
    def __init__(self, output_json: dict[str, Any]) -> None:
        self.output_json = output_json
        self.calls = 0
        self.closed = False

    async def complete_json(
        self,
        *,
        model_name: str,
        system_prompt: str,
        user_prompt: str,
        timeout_seconds: int,
    ) -> ProviderCompletion:
        self.calls += 1
        return ProviderCompletion(
            provider="openrouter",
            model_name=model_name,
            output_json=self.output_json,
            raw_text="{}",
            usage=ProviderUsage(input_tokens=10, output_tokens=5, cost_usd=0.1),
            latency_seconds=0.2,
        )

    async def close(self) -> None:
        self.closed = True


def _build_llm_config(*, max_calls_per_hour: int = 24) -> LLMConfig:
    return LLMConfig.model_validate(
        {
            "enabled": True,
            "provider": "openrouter",
            "timeout_seconds": 10,
            "budgets": {
                "max_calls_per_hour": max_calls_per_hour,
                "max_calls_per_day": max_calls_per_hour,
                "max_input_tokens_per_day": 1000000,
                "max_output_tokens_per_day": 1000000,
                "max_cost_usd_per_day": 100.0,
            },
            "routing": {
                "active_stack": "stack_a",
                "stack_a": {
                    "analyst_model": "test/model",
                    "critic_model": "critic/model",
                    "reporter_model": "reporter/model",
                },
                "stack_b": {
                    "analyst_model": "test/model-b",
                    "critic_model": "critic/model-b",
                    "reporter_model": "reporter/model-b",
                },
            },
        }
    )


@pytest.mark.asyncio
async def test_llm_service_processes_queue_and_persists_advice() -> None:
    repo = InMemoryAdviceRepo()
    provider = FakeProvider(
        output_json={
            "action": "no_trade",
            "confidence_pct": 64.0,
            "market_regime": "ranging",
            "smart_money_signal": "neutral",
            "trade_idea": None,
            "warnings": ["low momentum"],
            "evidence": ["flat OI"],
            "narrative": "Wait for cleaner displacement.",
            "recommended_focus_symbols": ["BTCUSDT"],
            "market_bias": "neutral",
            "setup_quality_score": 0.42,
        }
    )
    service = LLMAdvisoryService(
        config=_build_llm_config(),
        logger=structlog.get_logger("llm-service-test"),
        metrics=AppMetrics(),
        repository=repo,
        provider=provider,
    )
    await service.start(run_session_id="run-1")

    queued = service.enqueue_workflow(
        workflow="pre_session",
        payload={"note": "startup"},
        requested_at=datetime(2026, 3, 5, 10, 0, tzinfo=UTC),
    )
    assert queued is True

    await service.stop()

    assert provider.calls == 1
    assert provider.closed is True
    assert len(repo.items) == 1
    record = repo.items[0]
    assert record["advice_type"] == "pre_session"
    assert record["run_session_id"] == "run-1"
    assert record["output_json"]["advisor_output"]["market_regime"] == "ranging"


@pytest.mark.asyncio
async def test_llm_service_budget_block_skips_extra_calls() -> None:
    repo = InMemoryAdviceRepo()
    provider = FakeProvider(
        output_json={
            "action": "no_trade",
            "confidence_pct": 50.0,
            "market_regime": "neutral",
            "smart_money_signal": "neutral",
            "trade_idea": None,
            "warnings": [],
            "evidence": [],
            "narrative": "n/a",
            "recommended_focus_symbols": [],
            "market_bias": "neutral",
            "setup_quality_score": 0.1,
        }
    )
    service = LLMAdvisoryService(
        config=_build_llm_config(max_calls_per_hour=1),
        logger=structlog.get_logger("llm-budget-test"),
        metrics=AppMetrics(),
        repository=repo,
        provider=provider,
    )
    await service.start(run_session_id="run-2")

    assert service.enqueue_workflow(
        workflow="periodic",
        payload={},
        requested_at=datetime(2026, 3, 5, 10, 0, tzinfo=UTC),
    )
    assert service.enqueue_workflow(
        workflow="periodic",
        payload={},
        requested_at=datetime(2026, 3, 5, 10, 1, tzinfo=UTC),
    )

    await service.stop()

    assert provider.calls == 1
    assert len(repo.items) == 1
    assert repo.items[0]["advice_type"] == "periodic"


@pytest.mark.asyncio
async def test_llm_service_playbook_lifecycle() -> None:
    repo = InMemoryAdviceRepo()
    provider = FakeProvider(
        output_json={
            "action": "no_trade",
            "confidence_pct": 50.0,
            "market_regime": "neutral",
            "smart_money_signal": "neutral",
            "trade_idea": None,
            "warnings": [],
            "evidence": [],
            "narrative": "n/a",
            "recommended_focus_symbols": [],
            "market_bias": "neutral",
            "setup_quality_score": 0.1,
        }
    )
    service = LLMAdvisoryService(
        config=_build_llm_config(),
        logger=structlog.get_logger("llm-playbook-test"),
        metrics=AppMetrics(),
        repository=repo,
        provider=provider,
    )
    await service.start(run_session_id="run-3")

    set_reply = await service.playbook_set(text_or_json="avoid news spikes")
    show_reply = await service.playbook_show()
    clear_reply = await service.playbook_clear()
    show_after_clear = await service.playbook_show()
    await service.stop()

    assert set_reply == "Playbook updated."
    assert "avoid news spikes" in show_reply
    assert clear_reply == "Playbook cleared."
    assert show_after_clear == "Playbook is not set."
    assert [item["advice_type"] for item in repo.items if item["advice_type"].startswith("playbook")] == [
        "playbook_set",
        "playbook_clear",
    ]
