from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from trading_bot.domain.enums import Environment, ExchangeName, MarketType, PositionMode, RunMode


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RuntimeConfig(ConfigModel):
    service_name: str = "trading-bot"
    mode: RunMode
    environment: Environment


class ExchangeConfig(ConfigModel):
    primary: ExchangeName
    market_type: MarketType
    position_mode: PositionMode
    account_alias: str
    testnet: bool = True


class SymbolsConfig(ConfigModel):
    allowlist: list[str] = Field(default_factory=list)


class StorageConfig(ConfigModel):
    postgres_dsn: str = Field(min_length=1)
    redis_dsn: str = Field(min_length=1)


class ObservabilityConfig(ConfigModel):
    log_level: str
    http_host: str
    http_port: int = Field(ge=1, le=65535)


class StrategyDefaultsConfig(ConfigModel):
    name: str
    default_timeframe: str


class RiskDefaultsConfig(ConfigModel):
    max_open_positions: int = Field(ge=1)
    risk_per_trade: float = Field(gt=0)
    max_daily_loss: float = Field(gt=0)


class LLMConfig(ConfigModel):
    enabled: bool = False
    provider: str
    model_name: str
    timeout_seconds: int = Field(ge=1)


class AppSettings(ConfigModel):
    runtime: RuntimeConfig
    exchange: ExchangeConfig
    symbols: SymbolsConfig
    storage: StorageConfig
    observability: ObservabilityConfig
    strategy: StrategyDefaultsConfig
    risk: RiskDefaultsConfig
    llm: LLMConfig
