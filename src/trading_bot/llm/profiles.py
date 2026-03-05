from __future__ import annotations

from dataclasses import dataclass

from trading_bot.config.schema import LLMRoutingConfig


@dataclass(frozen=True, slots=True)
class ModelStackProfile:
    name: str
    analyst_model: str
    critic_model: str
    reporter_model: str


def active_profile(routing: LLMRoutingConfig) -> ModelStackProfile:
    stack = routing.active
    return ModelStackProfile(
        name=routing.active_stack,
        analyst_model=stack.analyst_model,
        critic_model=stack.critic_model,
        reporter_model=stack.reporter_model,
    )
