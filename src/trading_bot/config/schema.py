from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from trading_bot.domain.enums import Environment, ExchangeName, MarketType, PositionMode, RunMode


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RuntimeConfig(ConfigModel):
    service_name: str = "trading-bot"
    mode: RunMode
    environment: Environment
    dry_run: bool = False
    replay_source: str | None = None
    timezone: str = "UTC"


class ExchangeConfig(ConfigModel):
    primary: ExchangeName
    market_type: MarketType
    position_mode: PositionMode
    account_alias: str
    testnet: bool = True
    private_state_enabled: bool = False
    recv_window_ms: int = Field(default=5000, ge=1)


class SymbolsConfig(ConfigModel):
    allowlist: list[str] = Field(default_factory=list)


class MarketDataConfig(ConfigModel):
    orderbook_depth: int = Field(default=50, ge=1)
    kline_intervals: list[int] = Field(default_factory=lambda: [1, 5, 15])
    enable_trades: bool = True
    enable_ticker: bool = True
    enable_liquidations: bool = True
    enable_open_interest: bool = True
    enable_funding: bool = True
    bootstrap_kline_limit: int = Field(default=200, ge=1, le=1000)
    open_interest_poll_interval_seconds: int = Field(default=300, ge=1)
    funding_poll_interval_seconds: int = Field(default=300, ge=1)
    ws_reconnect_min_seconds: int = Field(default=1, ge=1)
    ws_reconnect_max_seconds: int = Field(default=30, ge=1)


class StorageConfig(ConfigModel):
    postgres_dsn: str = Field(min_length=1)
    redis_dsn: str = Field(min_length=1)
    market_archive_root: str = "data/market_archive"
    parquet_compression: str = "zstd"
    parquet_flush_rows: int = Field(default=5000, ge=1)
    parquet_flush_seconds: int = Field(default=5, ge=1)


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
    config_version: int = Field(default=1, ge=1)
    runtime: RuntimeConfig
    exchange: ExchangeConfig
    symbols: SymbolsConfig
    market_data: MarketDataConfig = Field(default_factory=MarketDataConfig)
    storage: StorageConfig
    observability: ObservabilityConfig
    strategy: StrategyDefaultsConfig
    risk: RiskDefaultsConfig
    llm: LLMConfig
