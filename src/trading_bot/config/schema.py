from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trading_bot.domain.enums import EntryType, Environment, ExchangeName, MarketType, PositionMode, RunMode
from trading_bot.timeframes import canonicalize_interval


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
    kline_intervals: list[str] = Field(default_factory=lambda: ["1m", "5m", "15m"])
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

    @field_validator("kline_intervals", mode="before")
    @classmethod
    def normalize_kline_intervals(cls, value: list[object] | None) -> list[str]:
        if value is None:
            return ["1m", "5m", "15m"]
        return [canonicalize_interval(item) for item in value]


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


class TelegramAlertsConfig(ConfigModel):
    enabled: bool = False
    chat_ids: list[int] = Field(default_factory=list)
    allowed_chat_ids: list[int] = Field(default_factory=list)
    allowed_user_ids: list[int] = Field(default_factory=list)
    poll_interval_seconds: int = Field(default=2, ge=1)
    long_poll_timeout_seconds: int = Field(default=15, ge=1, le=60)
    min_severity: Literal["info", "warning", "critical"] = "info"
    startup_enabled: bool = True
    shutdown_enabled: bool = True
    command_echo_enabled: bool = True
    risk_halt_enabled: bool = True
    protection_failure_enabled: bool = True

    @model_validator(mode="after")
    def align_allowed_chat_ids(self) -> "TelegramAlertsConfig":
        if not self.allowed_chat_ids and self.chat_ids:
            self.allowed_chat_ids = list(self.chat_ids)
        return self


class AlertsConfig(ConfigModel):
    telegram: TelegramAlertsConfig = Field(default_factory=TelegramAlertsConfig)


class PlaceholderStrategyConfig(ConfigModel):
    signal_threshold_bps: float = Field(default=8.0, ge=0)
    min_imbalance: float = Field(default=0.10, ge=0)
    max_hold_closed_klines: int = Field(default=3, ge=1)
    stop_loss_bps: float = Field(default=12.0, ge=0)
    take_profit_rr: float = Field(default=1.5, gt=0)


class SmcHistoryConfig(ConfigModel):
    entry_bars: int = Field(default=160, ge=16)
    structure_bars: int = Field(default=96, ge=16)
    bias_bars: int = Field(default=64, ge=16)
    orderbook_snapshots: int = Field(default=120, ge=3)
    oi_points: int = Field(default=32, ge=4)
    liquidation_events: int = Field(default=200, ge=1)


class SmcStructureConfig(ConfigModel):
    swing_lookback_bars: int = Field(default=2, ge=1)
    min_break_bps: float = Field(default=2.0, ge=0)
    max_signal_age_bars: int = Field(default=6, ge=1)


class SmcSweepConfig(ConfigModel):
    lookback_bars: int = Field(default=20, ge=3)
    reclaim_within_bars: int = Field(default=2, ge=1)
    min_penetration_bps: float = Field(default=3.0, ge=0)


class SmcFvgConfig(ConfigModel):
    min_gap_bps: float = Field(default=2.0, ge=0)
    max_age_bars: int = Field(default=8, ge=1)


class SmcOrderBlockConfig(ConfigModel):
    impulse_displacement_bps: float = Field(default=15.0, ge=0)
    max_age_bars: int = Field(default=12, ge=1)


class SmcOrderBookConfig(ConfigModel):
    imbalance_levels: int = Field(default=5, ge=1, le=50)
    min_abs_imbalance: float = Field(default=0.12, ge=0, le=1)
    wall_distance_bps: float = Field(default=20.0, ge=0)
    wall_size_vs_median: float = Field(default=3.0, ge=1)
    wall_min_persistence_snapshots: int = Field(default=3, ge=1)


class SmcOpenInterestConfig(ConfigModel):
    lookback_points: int = Field(default=3, ge=2)
    min_delta_bps: float = Field(default=5.0, ge=0)


class SmcFundingConfig(ConfigModel):
    enabled: bool = True
    adverse_threshold: Decimal = Field(default=Decimal("0.0005"))
    missing_is_neutral: bool = True


class SmcLiquidationsConfig(ConfigModel):
    enabled: bool = False
    burst_window_seconds: int = Field(default=5, ge=1)
    min_same_side_events: int = Field(default=3, ge=1)
    missing_is_neutral: bool = True


class SmcConfirmationsConfig(ConfigModel):
    min_support_count: int = Field(default=2, ge=1, le=3)


class SmcEntryConfig(ConfigModel):
    mode: str = "market_first"
    allow_limit_retest: bool = False
    max_setup_age_bars: int = Field(default=3, ge=1)


class SmcExitConfig(ConfigModel):
    max_hold_bars: int = Field(default=10, ge=1)
    invalidation_buffer_bps: float = Field(default=2.0, ge=0)
    take_profit_rr: float = Field(default=2.0, gt=0)


class SmcScalperV1Config(ConfigModel):
    bias_timeframe: str = "15m"
    structure_timeframe: str = "5m"
    entry_timeframe: str = "1m"
    history: SmcHistoryConfig = Field(default_factory=SmcHistoryConfig)
    structure: SmcStructureConfig = Field(default_factory=SmcStructureConfig)
    sweep: SmcSweepConfig = Field(default_factory=SmcSweepConfig)
    fvg: SmcFvgConfig = Field(default_factory=SmcFvgConfig)
    order_block: SmcOrderBlockConfig = Field(default_factory=SmcOrderBlockConfig)
    orderbook: SmcOrderBookConfig = Field(default_factory=SmcOrderBookConfig)
    open_interest: SmcOpenInterestConfig = Field(default_factory=SmcOpenInterestConfig)
    funding: SmcFundingConfig = Field(default_factory=SmcFundingConfig)
    liquidations: SmcLiquidationsConfig = Field(default_factory=SmcLiquidationsConfig)
    confirmations: SmcConfirmationsConfig = Field(default_factory=SmcConfirmationsConfig)
    entry: SmcEntryConfig = Field(default_factory=SmcEntryConfig)
    exit: SmcExitConfig = Field(default_factory=SmcExitConfig)

    @field_validator("bias_timeframe", "structure_timeframe", "entry_timeframe", mode="before")
    @classmethod
    def normalize_timeframe(cls, value: object) -> str:
        return canonicalize_interval(value)


class StrategyDefaultsConfig(ConfigModel):
    name: str = "phase3_placeholder"
    default_timeframe: str = "1m"
    placeholder: PlaceholderStrategyConfig = Field(default_factory=PlaceholderStrategyConfig)
    smc_scalper_v1: SmcScalperV1Config = Field(default_factory=SmcScalperV1Config)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_placeholder_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        placeholder = dict(normalized.get("placeholder", {})) if isinstance(normalized.get("placeholder"), dict) else {}
        legacy_mapping = {
            "placeholder_signal_threshold_bps": "signal_threshold_bps",
            "placeholder_min_imbalance": "min_imbalance",
            "placeholder_max_hold_closed_klines": "max_hold_closed_klines",
        }
        for legacy_key, nested_key in legacy_mapping.items():
            if legacy_key in normalized:
                legacy_value = normalized.pop(legacy_key)
                placeholder[nested_key] = legacy_value
        normalized["placeholder"] = placeholder
        return normalized

    @field_validator("default_timeframe", mode="before")
    @classmethod
    def normalize_default_timeframe(cls, value: object) -> str:
        return canonicalize_interval(value or "1m")

    @model_validator(mode="after")
    def align_default_timeframe(self) -> "StrategyDefaultsConfig":
        if self.name == "smc_scalper_v1":
            self.default_timeframe = self.smc_scalper_v1.entry_timeframe
        return self

    @property
    def placeholder_signal_threshold_bps(self) -> float:
        return self.placeholder.signal_threshold_bps

    @property
    def placeholder_min_imbalance(self) -> float:
        return self.placeholder.min_imbalance

    @property
    def placeholder_max_hold_closed_klines(self) -> int:
        return self.placeholder.max_hold_closed_klines


class RiskDefaultsConfig(ConfigModel):
    max_open_positions: int = Field(ge=1)
    risk_per_trade: float = Field(gt=0)
    max_daily_loss: float = Field(gt=0)
    stale_market_data_seconds: int = Field(default=2, ge=1)
    one_position_per_symbol: bool = True
    leverage_cap: Decimal = Field(default=Decimal("5"), gt=0)
    max_consecutive_losses: int = Field(default=3, ge=1)
    cooldown_minutes_after_loss_streak: int = Field(default=30, ge=0)
    funding_blackout_minutes_before: int = Field(default=5, ge=0)
    funding_blackout_minutes_after: int = Field(default=5, ge=0)


class ExecutionConfig(ConfigModel):
    default_entry_type: EntryType = EntryType.MARKET
    limit_ttl_ms: int = Field(default=3000, ge=1)
    market_slippage_guard_bps: float = Field(default=10.0, ge=0)
    max_market_data_age_ms: int = Field(default=2000, ge=1)
    reconciliation_interval_seconds: int = Field(default=5, ge=1)
    flatten_on_protection_failure: bool = True


class PaperConfig(ConfigModel):
    initial_equity_usdt: Decimal = Field(default=Decimal("10000"), gt=0)
    default_order_notional_usdt: Decimal = Field(default=Decimal("100"), gt=0)
    maker_fee_bps: float = Field(default=2.0, ge=0)
    taker_fee_bps: float = Field(default=5.5, ge=0)
    fill_latency_ms: int = Field(default=150, ge=0)
    limit_fill_visible_ratio: float = Field(default=0.25, gt=0, le=1)
    allow_partial_limit_fills: bool = True


class LiveConfig(ConfigModel):
    execution_enabled: bool = False
    allow_mainnet: bool = False
    symbol_allowlist: list[str] = Field(default_factory=list)
    max_order_notional_usdt: Decimal = Field(default=Decimal("100"), gt=0)
    max_position_notional_usdt: Decimal = Field(default=Decimal("100"), gt=0)
    max_total_exposure_usdt: Decimal = Field(default=Decimal("100"), gt=0)
    startup_recovery_timeout_seconds: int = Field(default=20, ge=1)
    private_state_stale_after_seconds: int = Field(default=10, ge=1)
    rest_resync_interval_seconds: int = Field(default=15, ge=1)
    cancel_ack_timeout_seconds: int = Field(default=5, ge=1)
    startup_recovery_policy: Literal["halt", "flatten"] = "halt"


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
    config_version: int = Field(default=5, ge=1)
    runtime: RuntimeConfig
    exchange: ExchangeConfig
    symbols: SymbolsConfig
    market_data: MarketDataConfig = Field(default_factory=MarketDataConfig)
    storage: StorageConfig
    observability: ObservabilityConfig
    alerts: AlertsConfig = Field(default_factory=AlertsConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    paper: PaperConfig = Field(default_factory=PaperConfig)
    live: LiveConfig = Field(default_factory=LiveConfig)
    replay: ReplayConfig = Field(default_factory=ReplayConfig)
    strategy: StrategyDefaultsConfig = Field(default_factory=StrategyDefaultsConfig)
    risk: RiskDefaultsConfig
    llm: LLMConfig
