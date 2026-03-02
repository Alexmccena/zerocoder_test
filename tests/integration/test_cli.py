from __future__ import annotations

import json

from typer.testing import CliRunner

from trading_bot.cli import app
from trading_bot.domain.enums import Environment, ServiceStatus
from trading_bot.domain.models import HealthReport


runner = CliRunner()


def apply_env(monkeypatch) -> None:
    monkeypatch.setenv("TB_ENV", "dev")
    monkeypatch.setenv("TB_CONFIG_FILE", "config/dev.yaml")
    monkeypatch.setenv("TB_POSTGRES_DSN", "postgresql+asyncpg://user:pass@localhost:5432/app")
    monkeypatch.setenv("TB_REDIS_DSN", "redis://localhost:6379/0")
    monkeypatch.setenv("TB_LOG_LEVEL", "INFO")
    monkeypatch.setenv("TB_HTTP_HOST", "0.0.0.0")
    monkeypatch.setenv("TB_HTTP_PORT", "8080")


def test_validate_config_command(monkeypatch) -> None:
    apply_env(monkeypatch)

    result = runner.invoke(app, ["validate-config"])

    assert result.exit_code == 0
    assert "fingerprint=" in result.stdout


def test_doctor_command_uses_health_report(monkeypatch) -> None:
    apply_env(monkeypatch)

    class FakeContainer:
        async def doctor_report(self) -> HealthReport:
            return HealthReport(
                status=ServiceStatus.OK,
                service="trading-bot",
                environment=Environment.TEST,
                checks={"config": ServiceStatus.OK, "postgres": ServiceStatus.OK, "redis": ServiceStatus.OK},
            )

        async def shutdown(self) -> None:
            return None

    monkeypatch.setattr("trading_bot.cli.build_container", lambda _bootstrap: FakeContainer())

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"


def test_db_upgrade_command_calls_alembic(monkeypatch) -> None:
    apply_env(monkeypatch)
    called = {}

    def fake_upgrade(dsn: str, revision: str) -> None:
        called["dsn"] = dsn
        called["revision"] = revision

    monkeypatch.setattr("trading_bot.cli.run_alembic_upgrade", fake_upgrade)

    result = runner.invoke(app, ["db", "upgrade"])

    assert result.exit_code == 0
    assert called["revision"] == "head"
    assert called["dsn"] == "postgresql+asyncpg://user:pass@localhost:5432/app"


def test_capture_command_uses_capture_container(monkeypatch) -> None:
    apply_env(monkeypatch)
    captured = {}

    class FakeCaptureContainer:
        async def run_capture(self, *, duration_seconds: int | None = None) -> None:
            captured["duration_seconds"] = duration_seconds

        async def shutdown(self) -> None:
            captured["shutdown"] = True

    def fake_build_capture_container(bootstrap, *, public_only: bool = False):
        captured["public_only"] = public_only
        captured["env"] = bootstrap.env.value
        return FakeCaptureContainer()

    monkeypatch.setattr("trading_bot.cli.build_capture_container", fake_build_capture_container)

    result = runner.invoke(app, ["capture", "--duration-seconds", "3", "--public-only"])

    assert result.exit_code == 0
    assert captured["duration_seconds"] == 3
    assert captured["public_only"] is True
    assert captured["shutdown"] is True
