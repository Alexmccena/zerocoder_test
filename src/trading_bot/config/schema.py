from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_bot.domain.enums import EntryType, Environment, ExchangeName, MarketType, PositionMode, RunMode


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
    name: str = "phase3_placeholder"
    default_timeframe: str = "1m"
    placeholder_signal_threshold_bps: float = Field(default=8.0, ge=0)
    placeholder_min_imbalance: float = Field(default=0.10, ge=0)
    placeholder_max_hold_closed_klines: int = Field(default=3, ge=1)


class RiskDefaultsConfig(ConfigModel):
    max_open_positions: int = Field(ge=1)
    risk_per_trade: float = Field(gt=0)
    max_daily_loss: float = Field(gt=0)
    stale_market_data_seconds: int = Field(default=2, ge=1)
    one_position_per_symbol: bool = True


class ExecutionConfig(ConfigModel):
    default_entry_type: EntryType = EntryType.MARKET
    limit_ttl_ms: int = Field(default=3000, ge=1)
    market_slippage_guard_bps: float = Field(default=10.0, ge=0)
    max_market_data_age_ms: int = Field(default=2000, ge=1)


class PaperConfig(ConfigModel):
    initial_equity_usdt: Decimal = Field(default=Decimal("10000"), gt=0)
    default_order_notional_usdt: Decimal = Field(default=Decimal("100"), gt=0)
    maker_fee_bps: float = Field(default=2.0, ge=0)
    taker_fee_bps: float = Field(default=5.5, ge=0)
    fill_latency_ms: int = Field(default=150, ge=0)
    limit_fill_visible_ratio: float = Field(default=0.25, gt=0, le=1)
    allow_partial_limit_fills: bool = True


class ReplayConfig(ConfigModel):
    source_root: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    speed: float = Field(default=1.0, gt=0)
    warmup_minutes: int = Field(default=15, ge=0)
    fail_on_gap: bool = True
    max_gap_seconds: int = Field(default=30, ge=1)

    @model_validator(mode="after")
    def validate_window(self) -> "ReplayConfig":
        if self.start_at is not None and self.end_at is not None and self.start_at >= self.end_at:
            raise ValueError("replay.start_at must be earlier than replay.end_at")
        return self


class LLMConfig(ConfigModel):
    enabled: bool = False
    provider: str
    model_name: str
    timeout_seconds: int = Field(ge=1)


class AppSettings(ConfigModel):
    config_version: int = Field(default=2, ge=1)
    runtime: RuntimeConfig
    exchange: ExchangeConfig
    symbols: SymbolsConfig
    market_data: MarketDataConfig = Field(default_factory=MarketDataConfig)
    storage: StorageConfig
    observability: ObservabilityConfig
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    paper: PaperConfig = Field(default_factory=PaperConfig)
    replay: ReplayConfig = Field(default_factory=ReplayConfig)
    strategy: StrategyDefaultsConfig = Field(default_factory=StrategyDefaultsConfig)
    risk: RiskDefaultsConfig
    llm: LLMConfig
