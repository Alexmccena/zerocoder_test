from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from trading_bot.config.schema import LLMBudgetsConfig


@dataclass(frozen=True, slots=True)
class BudgetSnapshot:
    calls_hour: int
    calls_day: int
    input_tokens_day: int
    output_tokens_day: int
    cost_usd_day: float
    hour_key: str
    day_key: str


@dataclass(frozen=True, slots=True)
class BudgetDecision:
    allowed: bool
    reason: str | None
    snapshot: BudgetSnapshot


class LLMBudgetGuard:
    def __init__(self, limits: LLMBudgetsConfig) -> None:
        self._limits = limits
        self._day_key = ""
        self._hour_key = ""
        self._calls_hour = 0
        self._calls_day = 0
        self._input_tokens_day = 0
        self._output_tokens_day = 0
        self._cost_usd_day = 0.0

    def _roll_time_windows(self, now: datetime) -> None:
        current_day_key = now.astimezone(UTC).date().isoformat()
        current_hour_key = now.astimezone(UTC).strftime("%Y-%m-%dT%H")
        if current_day_key != self._day_key:
            self._day_key = current_day_key
            self._calls_day = 0
            self._input_tokens_day = 0
            self._output_tokens_day = 0
            self._cost_usd_day = 0.0
        if current_hour_key != self._hour_key:
            self._hour_key = current_hour_key
            self._calls_hour = 0

    def snapshot(self, *, now: datetime) -> BudgetSnapshot:
        self._roll_time_windows(now)
        return BudgetSnapshot(
            calls_hour=self._calls_hour,
            calls_day=self._calls_day,
            input_tokens_day=self._input_tokens_day,
            output_tokens_day=self._output_tokens_day,
            cost_usd_day=self._cost_usd_day,
            hour_key=self._hour_key,
            day_key=self._day_key,
        )

    def allow(self, *, now: datetime) -> BudgetDecision:
        self._roll_time_windows(now)
        if self._calls_hour >= self._limits.max_calls_per_hour:
            return BudgetDecision(False, "max_calls_per_hour", self.snapshot(now=now))
        if self._calls_day >= self._limits.max_calls_per_day:
            return BudgetDecision(False, "max_calls_per_day", self.snapshot(now=now))
        if self._input_tokens_day >= self._limits.max_input_tokens_per_day:
            return BudgetDecision(False, "max_input_tokens_per_day", self.snapshot(now=now))
        if self._output_tokens_day >= self._limits.max_output_tokens_per_day:
            return BudgetDecision(False, "max_output_tokens_per_day", self.snapshot(now=now))
        if self._cost_usd_day >= self._limits.max_cost_usd_per_day:
            return BudgetDecision(False, "max_cost_usd_per_day", self.snapshot(now=now))
        return BudgetDecision(True, None, self.snapshot(now=now))

    def register_call(self, *, now: datetime) -> BudgetSnapshot:
        self._roll_time_windows(now)
        self._calls_hour += 1
        self._calls_day += 1
        return self.snapshot(now=now)

    def register_usage(
        self,
        *,
        now: datetime,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
    ) -> BudgetSnapshot:
        self._roll_time_windows(now)
        self._input_tokens_day += max(int(input_tokens), 0)
        self._output_tokens_day += max(int(output_tokens), 0)
        self._cost_usd_day += max(float(cost_usd), 0.0)
        return self.snapshot(now=now)
