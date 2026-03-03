from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from trading_bot.bootstrap.settings import BootstrapSettings, project_root
from trading_bot.config.schema import AppSettings
from trading_bot.domain.enums import RunMode


class ConfigLoadError(RuntimeError):
    """Raised when application configuration cannot be loaded."""


@dataclass(frozen=True, slots=True)
class LoadedConfig:
    settings: AppSettings
    fingerprint: str


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigLoadError(f"Configuration file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigLoadError(f"Invalid YAML in configuration file {path}: {exc}") from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigLoadError(f"Configuration file must contain a mapping at top level: {path}")
    return raw


def _default_strategy_document() -> dict[str, Any]:
    return {
        "name": "phase3_placeholder",
        "default_timeframe": "1m",
        "placeholder": {
            "signal_threshold_bps": 8.0,
            "min_imbalance": 0.10,
            "max_hold_closed_klines": 3,
            "stop_loss_bps": 12.0,
            "take_profit_rr": 1.5,
        },
        "smc_scalper_v1": {
            "bias_timeframe": "15m",
            "structure_timeframe": "5m",
            "entry_timeframe": "1m",
            "history": {
                "entry_bars": 160,
                "structure_bars": 96,
                "bias_bars": 64,
                "orderbook_snapshots": 120,
                "oi_points": 32,
                "liquidation_events": 200,
            },
            "structure": {
                "swing_lookback_bars": 2,
                "min_break_bps": 2.0,
                "max_signal_age_bars": 6,
            },
            "sweep": {
                "lookback_bars": 20,
                "reclaim_within_bars": 2,
                "min_penetration_bps": 3.0,
            },
            "fvg": {
                "min_gap_bps": 2.0,
                "max_age_bars": 8,
            },
            "order_block": {
                "impulse_displacement_bps": 15.0,
                "max_age_bars": 12,
            },
            "orderbook": {
                "imbalance_levels": 5,
                "min_abs_imbalance": 0.12,
                "wall_distance_bps": 20.0,
                "wall_size_vs_median": 3.0,
                "wall_min_persistence_snapshots": 3,
            },
            "open_interest": {
                "lookback_points": 3,
                "min_delta_bps": 5.0,
            },
            "funding": {
                "enabled": True,
                "adverse_threshold": "0.0005",
                "missing_is_neutral": True,
            },
            "liquidations": {
                "enabled": False,
                "burst_window_seconds": 5,
                "min_same_side_events": 3,
                "missing_is_neutral": True,
            },
            "confirmations": {
                "min_support_count": 2,
            },
            "entry": {
                "mode": "market_first",
                "allow_limit_retest": False,
                "max_setup_age_bars": 3,
            },
            "exit": {
                "max_hold_bars": 10,
                "invalidation_buffer_bps": 2.0,
                "take_profit_rr": 2.0,
            },
        },
    }


def _normalize_strategy_document(raw: dict[str, Any] | None) -> dict[str, Any]:
    strategy = dict(raw or {})
    placeholder = strategy.pop("placeholder", {})
    if not isinstance(placeholder, dict):
        placeholder = {}
    legacy_placeholder_mapping = {
        "placeholder_signal_threshold_bps": "signal_threshold_bps",
        "placeholder_min_imbalance": "min_imbalance",
        "placeholder_max_hold_closed_klines": "max_hold_closed_klines",
    }
    for legacy_key, nested_key in legacy_placeholder_mapping.items():
        if legacy_key in strategy:
            legacy_value = strategy.pop(legacy_key)
            placeholder[nested_key] = legacy_value

    smc = strategy.pop("smc_scalper_v1", {})
    if not isinstance(smc, dict):
        smc = {}

    normalized = _default_strategy_document()
    normalized = deep_merge(normalized, strategy)
    normalized["placeholder"] = deep_merge(normalized["placeholder"], placeholder)
    normalized["smc_scalper_v1"] = deep_merge(normalized["smc_scalper_v1"], smc)
    return normalized


def normalize_config_document(raw: dict[str, Any]) -> dict[str, Any]:
    normalized = deep_merge(
        {
            "config_version": 4,
            "execution": {
                "default_entry_type": "market",
                "limit_ttl_ms": 3000,
                "market_slippage_guard_bps": 10.0,
                "max_market_data_age_ms": 2000,
                "reconciliation_interval_seconds": 5,
                "flatten_on_protection_failure": True,
            },
            "paper": {
                "initial_equity_usdt": "10000",
                "default_order_notional_usdt": "100",
                "maker_fee_bps": 2.0,
                "taker_fee_bps": 5.5,
                "fill_latency_ms": 150,
                "limit_fill_visible_ratio": 0.25,
                "allow_partial_limit_fills": True,
            },
            "replay": {
                "source_root": None,
                "start_at": None,
                "end_at": None,
                "speed": 1.0,
                "warmup_minutes": 15,
                "fail_on_gap": True,
                "max_gap_seconds": 30,
            },
            "alerts": {
                "telegram": {
                    "enabled": False,
                    "chat_ids": [],
                    "allowed_chat_ids": [],
                    "allowed_user_ids": [],
                    "poll_interval_seconds": 2,
                    "long_poll_timeout_seconds": 15,
                    "min_severity": "info",
                    "startup_enabled": True,
                    "shutdown_enabled": True,
                    "command_echo_enabled": True,
                    "risk_halt_enabled": True,
                    "protection_failure_enabled": True,
                }
            },
            "strategy": _default_strategy_document(),
            "risk": {
                "stale_market_data_seconds": 2,
                "one_position_per_symbol": True,
                "leverage_cap": "5",
                "max_consecutive_losses": 3,
                "cooldown_minutes_after_loss_streak": 30,
                "funding_blackout_minutes_before": 5,
                "funding_blackout_minutes_after": 5,
            },
        },
        raw,
    )
    normalized["config_version"] = max(int(normalized.get("config_version", 1)), 4)
    normalized["strategy"] = _normalize_strategy_document(normalized.get("strategy"))
    replay = normalized.setdefault("replay", {})
    if replay.get("source_root") is None:
        alias = normalized.get("runtime", {}).get("replay_source")
        if alias is not None:
            replay["source_root"] = alias
    return normalized


def build_env_overrides(env_settings: BootstrapSettings) -> dict[str, Any]:
    return {
        "runtime": {"environment": env_settings.env.value},
        "storage": {
            "postgres_dsn": env_settings.postgres_dsn,
            "redis_dsn": env_settings.redis_dsn,
        },
        "observability": {
            "log_level": env_settings.log_level,
            "http_host": env_settings.http_host,
            "http_port": env_settings.http_port,
        },
    }


def validate_runtime_secrets(settings: AppSettings, env_settings: BootstrapSettings) -> None:
    if settings.runtime.mode in {RunMode.CAPTURE, RunMode.LIVE} and settings.exchange.private_state_enabled and (
        not env_settings.bybit_api_key or not env_settings.bybit_api_secret
    ):
        raise ConfigLoadError(
            "TB_BYBIT_API_KEY and TB_BYBIT_API_SECRET are required when "
            "exchange.private_state_enabled=true"
        )
    telegram = settings.alerts.telegram
    if settings.runtime.mode in {RunMode.PAPER, RunMode.LIVE} and telegram.enabled:
        if not env_settings.telegram_bot_token:
            raise ConfigLoadError("TB_TELEGRAM_BOT_TOKEN is required when alerts.telegram.enabled=true")
        if not telegram.chat_ids:
            raise ConfigLoadError("alerts.telegram.chat_ids is required when alerts.telegram.enabled=true")


def compute_config_hash(payload: AppSettings | dict[str, Any]) -> str:
    normalized = payload.model_dump(mode="json") if isinstance(payload, AppSettings) else payload
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_app_config(
    env_settings: BootstrapSettings,
    *,
    base_file: Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> LoadedConfig:
    base_path = base_file or project_root() / "config" / "base.yaml"
    overlay_path = env_settings.resolved_config_file
    merged = normalize_config_document(
        deep_merge(
            _read_yaml(base_path),
            _read_yaml(overlay_path),
        )
    )
    merged = deep_merge(merged, build_env_overrides(env_settings))
    if overrides is not None:
        merged = deep_merge(merged, overrides)
    try:
        app_settings = AppSettings.model_validate(merged)
    except ValidationError as exc:
        raise ConfigLoadError(f"Invalid application configuration: {exc}") from exc
    if app_settings.runtime.mode in {RunMode.REPLAY, RunMode.BACKTEST} and not app_settings.replay.source_root:
        raise ConfigLoadError("replay.source_root is required when runtime.mode is replay or backtest")
    validate_runtime_secrets(app_settings, env_settings)
    return LoadedConfig(settings=app_settings, fingerprint=compute_config_hash(app_settings))
