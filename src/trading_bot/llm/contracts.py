from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


AdviceWorkflow = Literal[
    "pre_session",
    "periodic",
    "post_trade",
    "risk_halt",
    "operator_analyze",
]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlaybookState(ContractModel):
    text: str
    constraints_json: dict[str, Any] = Field(default_factory=dict)
    source: Literal["telegram", "runtime"] = "telegram"
    updated_at: datetime = Field(default_factory=utc_now)
    run_session_id: str | None = None


class AdvisorContext(ContractModel):
    workflow: AdviceWorkflow
    run_session_id: str
    symbol: str | None = None
    requested_at: datetime = Field(default_factory=utc_now)
    language: Literal["en", "ru", "bi"] = "en"
    payload: dict[str, Any] = Field(default_factory=dict)
    operator_prompt: str | None = None
    playbook: PlaybookState | None = None


class ProviderUsage(ContractModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class ProviderCompletion(ContractModel):
    provider: str
    model_name: str
    output_json: dict[str, Any]
    raw_text: str
    usage: ProviderUsage = Field(default_factory=ProviderUsage)
    latency_seconds: float = Field(default=0.0, ge=0.0)
