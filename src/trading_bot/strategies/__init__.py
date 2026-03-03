from __future__ import annotations

from typing import Callable

from trading_bot.config.schema import AppSettings
from trading_bot.domain.models import RuntimeState
from trading_bot.strategies.phase3_placeholder import Phase3PlaceholderStrategy
from trading_bot.strategies.smc_scalper_v1 import SmcScalperV1Strategy


def build_strategy(*, config: AppSettings, runtime_state_provider: Callable[[], RuntimeState]):
    if config.strategy.name == "phase3_placeholder":
        return Phase3PlaceholderStrategy(config=config, runtime_state_provider=runtime_state_provider)
    if config.strategy.name == "smc_scalper_v1":
        return SmcScalperV1Strategy(config=config, runtime_state_provider=runtime_state_provider)
    raise ValueError(f"unknown strategy configured: {config.strategy.name}")


__all__ = ["Phase3PlaceholderStrategy", "SmcScalperV1Strategy", "build_strategy"]
