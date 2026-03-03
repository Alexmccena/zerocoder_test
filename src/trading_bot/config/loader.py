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


def normalize_config_document(raw: dict[str, Any]) -> dict[str, Any]:
    normalized = deep_merge(
        {
            "config_version": 2,
            "execution": {
                "default_entry_type": "market",
                "limit_ttl_ms": 3000,
                "market_slippage_guard_bps": 10.0,
                "max_market_data_age_ms": 2000,
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
            "strategy": {
                "name": "phase3_placeholder",
                "default_timeframe": "1m",
                "placeholder_signal_threshold_bps": 8.0,
                "placeholder_min_imbalance": 0.10,
                "placeholder_max_hold_closed_klines": 3,
            },
            "risk": {
                "stale_market_data_seconds": 2,
                "one_position_per_symbol": True,
            },
        },
        raw,
    )
    if normalized.get("config_version", 1) < 2:
        normalized["config_version"] = 2
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
    merged = deep_merge(
        normalize_config_document(_read_yaml(base_path)),
        normalize_config_document(_read_yaml(overlay_path)),
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
