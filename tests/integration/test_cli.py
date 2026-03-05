from __future__ import annotations

import json

from typer.testing import CliRunner

from trading_bot.cli import app
from trading_bot.domain.enums import Environment, RunMode, ServiceStatus
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
    monkeypatch.setenv("TB_BYBIT_API_KEY", "key")
    monkeypatch.setenv("TB_BYBIT_API_SECRET", "secret")


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


def test_run_command_uses_runtime_container(monkeypatch) -> None:
    apply_env(monkeypatch)
    captured = {}

    class FakeRuntimeContainer:
        async def run_runtime(self, *, duration_seconds: int | None = None, summary_out=None) -> dict[str, object]:
            captured["duration_seconds"] = duration_seconds
            captured["summary_out"] = summary_out
            return {"ok": True}

        async def shutdown(self) -> None:
            captured["shutdown"] = True

    def fake_build_runtime_container(bootstrap, *, mode: RunMode, source=None, start_at=None, end_at=None, speed=None):
        captured["env"] = bootstrap.env.value
        captured["mode"] = mode
        captured["source"] = source
        return FakeRuntimeContainer()

    monkeypatch.setattr("trading_bot.cli.build_runtime_container", fake_build_runtime_container)

    result = runner.invoke(app, ["run", "--mode", "paper", "--duration-seconds", "5"])

    assert result.exit_code == 0
    assert captured["mode"] == RunMode.PAPER
    assert captured["duration_seconds"] == 5
    assert captured["shutdown"] is True


def test_soak_paper_command_uses_runtime_container(monkeypatch, tmp_path) -> None:
    apply_env(monkeypatch)
    captured = {}
    summary_path = tmp_path / "summary.json"

    class FakeRuntimeContainer:
        async def run_runtime(self, *, duration_seconds: int | None = None, summary_out=None) -> dict[str, object]:
            captured["duration_seconds"] = duration_seconds
            captured["summary_out"] = summary_out
            return {"mode": "paper"}

        async def shutdown(self) -> None:
            captured["shutdown"] = True

    def fake_build_runtime_container(bootstrap, *, mode: RunMode, source=None, start_at=None, end_at=None, speed=None):
        captured["mode"] = mode
        return FakeRuntimeContainer()

    monkeypatch.setattr("trading_bot.cli.build_runtime_container", fake_build_runtime_container)

    result = runner.invoke(
        app,
        ["soak-paper", "--duration-seconds", "10", "--summary-out", str(summary_path)],
    )

    assert result.exit_code == 0
    assert captured["mode"] == RunMode.PAPER
    assert captured["duration_seconds"] == 10
    assert captured["summary_out"] == summary_path
    assert captured["shutdown"] is True


def test_run_live_uses_runtime_container(monkeypatch) -> None:
    apply_env(monkeypatch)
    captured = {}

    class FakeRuntimeContainer:
        async def run_runtime(self, *, duration_seconds: int | None = None, summary_out=None) -> dict[str, object]:
            captured["duration_seconds"] = duration_seconds
            return {"mode": "live"}

        async def shutdown(self) -> None:
            captured["shutdown"] = True

    def fake_build_runtime_container(bootstrap, *, mode: RunMode, source=None, start_at=None, end_at=None, speed=None):
        captured["mode"] = mode
        return FakeRuntimeContainer()

    monkeypatch.setattr("trading_bot.cli.build_runtime_container", fake_build_runtime_container)

    result = runner.invoke(app, ["run", "--mode", "live", "--duration-seconds", "5"])

    assert result.exit_code == 0
    assert captured["mode"] == RunMode.LIVE
    assert captured["duration_seconds"] == 5
    assert captured["shutdown"] is True


def test_live_preflight_command_uses_runtime_container(monkeypatch) -> None:
    apply_env(monkeypatch)
    monkeypatch.setenv("TB_CONFIG_FILE", "config/live_testnet.yaml")
    captured = {}

    class FakeRuntimeContainer:
        async def live_preflight(self) -> dict[str, object]:
            captured["called"] = True
            return {"mode": "live", "ws_auth_ok": True}

        async def shutdown(self) -> None:
            captured["shutdown"] = True

    def fake_build_runtime_container(bootstrap, *, mode: RunMode, source=None, start_at=None, end_at=None, speed=None):
        captured["mode"] = mode
        return FakeRuntimeContainer()

    monkeypatch.setattr("trading_bot.cli.build_runtime_container", fake_build_runtime_container)

    result = runner.invoke(app, ["live-preflight"])

    assert result.exit_code == 0
    assert captured["mode"] == RunMode.LIVE
    assert captured["called"] is True
    assert captured["shutdown"] is True
    payload = json.loads(result.stdout)
    assert payload["ws_auth_ok"] is True


def test_replay_command_uses_runtime_container(monkeypatch) -> None:
    apply_env(monkeypatch)
    captured = {}

    class FakeRuntimeContainer:
        async def run_runtime(self, *, duration_seconds: int | None = None, summary_out=None) -> dict[str, object]:
            captured["duration_seconds"] = duration_seconds
            return {"mode": "replay"}

        async def shutdown(self) -> None:
            captured["shutdown"] = True

    def fake_build_runtime_container(bootstrap, *, mode: RunMode, source=None, start_at=None, end_at=None, speed=None):
        captured["mode"] = mode
        captured["source"] = source
        captured["speed"] = speed
        return FakeRuntimeContainer()

    monkeypatch.setattr("trading_bot.cli.build_runtime_container", fake_build_runtime_container)

    result = runner.invoke(app, ["replay", "--source", "tests/fixtures/replay", "--speed", "20", "--duration-seconds", "2"])

    assert result.exit_code == 0
    assert captured["mode"] == RunMode.REPLAY
    assert captured["source"] == "tests/fixtures/replay"
    assert captured["speed"] == 20.0
    assert captured["duration_seconds"] == 2


def test_backtest_command_uses_runtime_container(monkeypatch, tmp_path) -> None:
    apply_env(monkeypatch)
    captured = {}
    summary_path = tmp_path / "summary.json"

    class FakeRuntimeContainer:
        async def run_runtime(self, *, duration_seconds: int | None = None, summary_out=None) -> dict[str, object]:
            captured["summary_out"] = summary_out
            return {"mode": "backtest"}

        async def shutdown(self) -> None:
            captured["shutdown"] = True

    def fake_build_runtime_container(bootstrap, *, mode: RunMode, source=None, start_at=None, end_at=None, speed=None):
        captured["mode"] = mode
        captured["source"] = source
        return FakeRuntimeContainer()

    monkeypatch.setattr("trading_bot.cli.build_runtime_container", fake_build_runtime_container)

    result = runner.invoke(app, ["backtest", "--source", "tests/fixtures/replay", "--summary-out", str(summary_path)])

    assert result.exit_code == 0
    assert captured["mode"] == RunMode.BACKTEST
    assert captured["source"] == "tests/fixtures/replay"
    assert captured["summary_out"] == summary_path
    assert captured["shutdown"] is True
