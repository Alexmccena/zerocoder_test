from __future__ import annotations

from datetime import UTC, datetime, timedelta

from trading_bot.config.schema import LLMBudgetsConfig
from trading_bot.llm.budget_guard import LLMBudgetGuard


def test_budget_guard_enforces_hour_and_day_call_limits() -> None:
    guard = LLMBudgetGuard(
        LLMBudgetsConfig(
            max_calls_per_hour=2,
            max_calls_per_day=3,
            max_input_tokens_per_day=1000,
            max_output_tokens_per_day=1000,
            max_cost_usd_per_day=10.0,
        )
    )
    start = datetime(2026, 3, 5, 10, 0, tzinfo=UTC)

    assert guard.allow(now=start).allowed is True
    guard.register_call(now=start)
    assert guard.allow(now=start + timedelta(minutes=1)).allowed is True
    guard.register_call(now=start + timedelta(minutes=1))
    blocked_hour = guard.allow(now=start + timedelta(minutes=2))
    assert blocked_hour.allowed is False
    assert blocked_hour.reason == "max_calls_per_hour"

    next_hour = start + timedelta(hours=1)
    assert guard.allow(now=next_hour).allowed is True
    guard.register_call(now=next_hour)
    blocked_day = guard.allow(now=next_hour + timedelta(minutes=1))
    assert blocked_day.allowed is False
    assert blocked_day.reason == "max_calls_per_day"


def test_budget_guard_enforces_tokens_and_cost_limits() -> None:
    guard = LLMBudgetGuard(
        LLMBudgetsConfig(
            max_calls_per_hour=10,
            max_calls_per_day=10,
            max_input_tokens_per_day=10,
            max_output_tokens_per_day=5,
            max_cost_usd_per_day=1.0,
        )
    )
    now = datetime(2026, 3, 5, 10, 0, tzinfo=UTC)

    guard.register_usage(now=now, input_tokens=10, output_tokens=0, cost_usd=0.0)
    blocked_input = guard.allow(now=now)
    assert blocked_input.allowed is False
    assert blocked_input.reason == "max_input_tokens_per_day"

    later = now + timedelta(days=1)
    assert guard.allow(now=later).allowed is True
    guard.register_usage(now=later, input_tokens=0, output_tokens=5, cost_usd=0.0)
    blocked_output = guard.allow(now=later)
    assert blocked_output.allowed is False
    assert blocked_output.reason == "max_output_tokens_per_day"

    next_day = later + timedelta(days=1)
    guard.register_usage(now=next_day, input_tokens=0, output_tokens=0, cost_usd=1.0)
    blocked_cost = guard.allow(now=next_day)
    assert blocked_cost.allowed is False
    assert blocked_cost.reason == "max_cost_usd_per_day"
