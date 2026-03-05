from __future__ import annotations

from typer.testing import CliRunner

from trading_bot.cli import app


runner = CliRunner()


def test_validate_config_smoke(monkeypatch) -> None:
    monkeypatch.setenv("TB_ENV", "dev")
    monkeypatch.setenv("TB_CONFIG_FILE", "config/dev.yaml")
    monkeypatch.setenv("TB_POSTGRES_DSN", "postgresql+asyncpg://user:pass@localhost:5432/app")
    monkeypatch.setenv("TB_REDIS_DSN", "redis://localhost:6379/0")
    monkeypatch.setenv("TB_LOG_LEVEL", "INFO")
    monkeypatch.setenv("TB_HTTP_HOST", "0.0.0.0")
    monkeypatch.setenv("TB_HTTP_PORT", "8080")
    monkeypatch.setenv("TB_OPENROUTER_API_KEY", "placeholder")

    result = runner.invoke(app, ["validate-config"])

    assert result.exit_code == 0
