from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from trading_bot.bootstrap.settings import BootstrapSettings
from trading_bot.config.loader import ConfigLoadError, compute_config_hash, load_app_config
from trading_bot.domain.enums import Environment


def test_load_app_config_merges_base_overlay_and_env(tmp_path: Path) -> None:
    base = tmp_path / "base.yaml"
    overlay = tmp_path / "dev.yaml"
    base.write_text(
        "\n".join(
            [
                "runtime:",
                "  service_name: trading-bot",
                "  mode: paper",
                "  environment: dev",
                "exchange:",
                "  primary: bybit",
                "  market_type: linear_perp",
                "  position_mode: one_way",
                "  account_alias: base",
                "  testnet: true",
                "symbols:",
                "  allowlist: [BTCUSDT]",
                "storage:",
                "  postgres_dsn: placeholder",
                "  redis_dsn: placeholder",
                "observability:",
                "  log_level: INFO",
                "  http_host: 127.0.0.1",
                "  http_port: 8000",
                "strategy:",
                "  name: foundation",
                "  default_timeframe: 1m",
                "risk:",
                "  max_open_positions: 2",
                "  risk_per_trade: 0.1",
                "  max_daily_loss: 0.2",
                "llm:",
                "  enabled: false",
                "  provider: none",
                "  model_name: ''",
                "  timeout_seconds: 10",
            ]
        ),
        encoding="utf-8",
    )
    overlay.write_text(
        "\n".join(
            [
                "exchange:",
                "  account_alias: overlay",
                "symbols:",
                "  allowlist: [BTCUSDT, ETHUSDT]",
                "observability:",
                "  log_level: DEBUG",
            ]
        ),
        encoding="utf-8",
    )
    settings = BootstrapSettings(
        env=Environment.DEV,
        config_file=str(overlay),
        postgres_dsn="postgresql+asyncpg://user:pass@localhost:5432/app",
        redis_dsn="redis://localhost:6379/0",
        log_level="WARNING",
        http_host="0.0.0.0",
        http_port=8080,
        _env_file=None,
    )

    loaded = load_app_config(settings, base_file=base)

    assert loaded.settings.exchange.account_alias == "overlay"
    assert loaded.settings.symbols.allowlist == ["BTCUSDT", "ETHUSDT"]
    assert loaded.settings.storage.postgres_dsn == "postgresql+asyncpg://user:pass@localhost:5432/app"
    assert loaded.settings.observability.log_level == "WARNING"


def test_invalid_yaml_raises_config_error(tmp_path: Path) -> None:
    base = tmp_path / "base.yaml"
    overlay = tmp_path / "dev.yaml"
    base.write_text("runtime: [", encoding="utf-8")
    overlay.write_text("{}", encoding="utf-8")
    settings = BootstrapSettings(
        env=Environment.DEV,
        config_file=str(overlay),
        postgres_dsn="postgresql+asyncpg://user:pass@localhost:5432/app",
        redis_dsn="redis://localhost:6379/0",
        _env_file=None,
    )

    with pytest.raises(ConfigLoadError):
        load_app_config(settings, base_file=base)


def test_bootstrap_settings_require_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in [
        "TB_ENV",
        "TB_CONFIG_FILE",
        "TB_POSTGRES_DSN",
        "TB_REDIS_DSN",
    ]:
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(ValidationError):
        BootstrapSettings(_env_file=None)


def test_config_hash_is_stable(tmp_path: Path) -> None:
    base = tmp_path / "base.yaml"
    overlay = tmp_path / "dev.yaml"
    base.write_text(
        "\n".join(
            [
                "runtime: {service_name: trading-bot, mode: paper, environment: dev}",
                "exchange: {primary: bybit, market_type: linear_perp, position_mode: one_way, account_alias: base, testnet: true}",
                "symbols: {allowlist: [BTCUSDT]}",
                "storage: {postgres_dsn: placeholder, redis_dsn: placeholder}",
                "observability: {log_level: INFO, http_host: 127.0.0.1, http_port: 8000}",
                "strategy: {name: foundation, default_timeframe: 1m}",
                "risk: {max_open_positions: 2, risk_per_trade: 0.1, max_daily_loss: 0.2}",
                "llm: {enabled: false, provider: none, model_name: '', timeout_seconds: 10}",
            ]
        ),
        encoding="utf-8",
    )
    overlay.write_text("{}", encoding="utf-8")
    settings = BootstrapSettings(
        env=Environment.DEV,
        config_file=str(overlay),
        postgres_dsn="postgresql+asyncpg://user:pass@localhost:5432/app",
        redis_dsn="redis://localhost:6379/0",
        _env_file=None,
    )

    loaded = load_app_config(settings, base_file=base)

    assert compute_config_hash(loaded.settings) == loaded.fingerprint


def test_private_state_requires_bybit_credentials(tmp_path: Path) -> None:
    base = tmp_path / "base.yaml"
    overlay = tmp_path / "dev.yaml"
    base.write_text(
        "\n".join(
            [
                "runtime: {service_name: trading-bot, mode: paper, environment: dev}",
                "exchange: {primary: bybit, market_type: linear_perp, position_mode: one_way, account_alias: base, testnet: true, private_state_enabled: false, recv_window_ms: 5000}",
                "symbols: {allowlist: [BTCUSDT]}",
                "storage: {postgres_dsn: placeholder, redis_dsn: placeholder}",
                "observability: {log_level: INFO, http_host: 127.0.0.1, http_port: 8000}",
                "strategy: {name: foundation, default_timeframe: 1m}",
                "risk: {max_open_positions: 2, risk_per_trade: 0.1, max_daily_loss: 0.2}",
                "llm: {enabled: false, provider: none, model_name: '', timeout_seconds: 10}",
            ]
        ),
        encoding="utf-8",
    )
    overlay.write_text("exchange: {private_state_enabled: true}", encoding="utf-8")
    settings = BootstrapSettings(
        env=Environment.DEV,
        config_file=str(overlay),
        postgres_dsn="postgresql+asyncpg://user:pass@localhost:5432/app",
        redis_dsn="redis://localhost:6379/0",
        _env_file=None,
    )

    with pytest.raises(ConfigLoadError, match="TB_BYBIT_API_KEY"):
        load_app_config(settings, base_file=base)
