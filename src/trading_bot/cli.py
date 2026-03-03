from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import typer
import uvicorn

from trading_bot.app import create_app
from trading_bot.bootstrap.container import build_capture_container, build_container, build_runtime_container
from trading_bot.bootstrap.settings import BootstrapSettings
from trading_bot.config.loader import LoadedConfig, load_app_config
from trading_bot.domain.enums import RunMode, ServiceStatus
from trading_bot.domain.models import HealthReport
from trading_bot.observability.metrics import AppMetrics
from trading_bot.storage.db import run_alembic_current, run_alembic_upgrade


app = typer.Typer(help="Trading bot CLI.")
db_app = typer.Typer(help="Database commands.")
app.add_typer(db_app, name="db")


class DoctorContainer(Protocol):
    async def doctor_report(self) -> HealthReport: ...

    async def shutdown(self) -> None: ...


@dataclass(frozen=True, slots=True)
class LoadedCliConfig:
    bootstrap: BootstrapSettings
    loaded: LoadedConfig


def _load_config_or_exit(*, overrides: dict[str, object] | None = None) -> LoadedCliConfig:
    metrics = AppMetrics()
    try:
        bootstrap = BootstrapSettings()
        loaded = load_app_config(bootstrap, overrides=overrides)
    except Exception as exc:  # pragma: no cover - exercised via CLI tests
        metrics.record_config_validation_failure()
        typer.echo(f"Configuration error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    return LoadedCliConfig(bootstrap=bootstrap, loaded=loaded)


async def _doctor(container: DoctorContainer) -> str:
    try:
        report = await container.doctor_report()
        return json.dumps(report.model_dump(mode="json"), indent=2)
    finally:
        shutdown = getattr(container, "shutdown", None)
        if shutdown is not None:
            await shutdown()


async def _run_capture(container, duration_seconds: int | None) -> None:
    try:
        await container.run_capture(duration_seconds=duration_seconds)
    finally:
        shutdown = getattr(container, "shutdown", None)
        if shutdown is not None:
            await shutdown()


async def _run_runtime(container, *, duration_seconds: int | None = None, summary_out: Path | None = None) -> dict[str, object]:
    try:
        return await container.run_runtime(duration_seconds=duration_seconds, summary_out=summary_out)
    finally:
        shutdown = getattr(container, "shutdown", None)
        if shutdown is not None:
            await shutdown()


@app.command("validate-config")
def validate_config() -> None:
    loaded = _load_config_or_exit()
    typer.echo(f"Config valid. fingerprint={loaded.loaded.fingerprint}")


@app.command()
def doctor() -> None:
    loaded = _load_config_or_exit()
    container = build_container(loaded.bootstrap)
    payload = asyncio.run(_doctor(container))
    typer.echo(payload)
    if json.loads(payload)["status"] != ServiceStatus.OK.value:
        raise typer.Exit(code=1)


@app.command()
def serve(
    host: str | None = typer.Option(default=None, help="Override configured HTTP host."),
    port: int | None = typer.Option(default=None, help="Override configured HTTP port."),
) -> None:
    loaded = _load_config_or_exit()
    container = build_container(loaded.bootstrap)
    uvicorn.run(
        create_app(container),
        host=host or container.config.observability.http_host,
        port=port or container.config.observability.http_port,
    )


@app.command()
def run(
    mode: RunMode | None = typer.Option(default=None, help="Runtime mode: paper or live."),
    duration_seconds: int | None = typer.Option(
        default=None,
        help="Optional finite runtime for smoke/testing paper runs.",
    ),
) -> None:
    loaded = _load_config_or_exit()
    effective_mode = mode or loaded.loaded.settings.runtime.mode
    if effective_mode == RunMode.LIVE:
        typer.echo("Runtime error: live mode is not implemented until phase 6.", err=True)
        raise typer.Exit(code=1)
    if effective_mode != RunMode.PAPER:
        typer.echo("Runtime error: bot run currently supports only paper or live.", err=True)
        raise typer.Exit(code=1)
    container = build_runtime_container(loaded.bootstrap, mode=effective_mode)
    asyncio.run(_run_runtime(container, duration_seconds=duration_seconds))


@app.command("soak-paper")
def soak_paper(
    duration_seconds: int = typer.Option(
        default=72 * 60 * 60,
        help="Wall-clock duration for the paper soak run.",
    ),
    summary_out: Path | None = typer.Option(default=None, help="Optional path for summary JSON."),
) -> None:
    overrides: dict[str, object] = {
        "runtime": {"mode": "paper"},
        "exchange": {"private_state_enabled": False},
    }
    loaded = _load_config_or_exit(overrides=overrides)
    container = build_runtime_container(loaded.bootstrap, mode=RunMode.PAPER)
    summary = asyncio.run(_run_runtime(container, duration_seconds=duration_seconds, summary_out=summary_out))
    typer.echo(json.dumps(summary, indent=2))


@app.command()
def capture(
    duration_seconds: int | None = typer.Option(
        default=None,
        help="Optional finite runtime for smoke/testing capture runs.",
    ),
    public_only: bool = typer.Option(
        default=False,
        help="Disable private-state capture even if enabled in config.",
    ),
) -> None:
    overrides: dict[str, object] = {"runtime": {"mode": "capture"}}
    if public_only:
        overrides["exchange"] = {"private_state_enabled": False}
    loaded = _load_config_or_exit(overrides=overrides)
    container = build_capture_container(loaded.bootstrap, public_only=public_only)
    asyncio.run(_run_capture(container, duration_seconds))


@app.command()
def replay(
    source: str | None = typer.Option(default=None, help="Replay source root."),
    start_at: str | None = typer.Option(default=None, help="Replay window start, ISO8601 UTC."),
    end_at: str | None = typer.Option(default=None, help="Replay window end, ISO8601 UTC."),
    speed: float | None = typer.Option(default=None, help="Replay speed multiplier."),
    duration_seconds: int | None = typer.Option(default=None, help="Optional wall-clock duration cap."),
) -> None:
    replay_overrides: dict[str, object] = {
        "source_root": source,
        "start_at": start_at,
        "end_at": end_at,
    }
    if speed is not None:
        replay_overrides["speed"] = speed
    overrides: dict[str, object] = {
        "runtime": {"mode": "replay"},
        "exchange": {"private_state_enabled": False},
        "replay": replay_overrides,
    }
    loaded = _load_config_or_exit(overrides=overrides)
    container = build_runtime_container(
        loaded.bootstrap,
        mode=RunMode.REPLAY,
        source=source,
        start_at=start_at,
        end_at=end_at,
        speed=speed,
    )
    summary = asyncio.run(_run_runtime(container, duration_seconds=duration_seconds))
    typer.echo(json.dumps(summary, indent=2))


@app.command()
def backtest(
    source: str | None = typer.Option(default=None, help="Backtest source root."),
    start_at: str | None = typer.Option(default=None, help="Backtest window start, ISO8601 UTC."),
    end_at: str | None = typer.Option(default=None, help="Backtest window end, ISO8601 UTC."),
    summary_out: Path | None = typer.Option(default=None, help="Optional path for summary JSON."),
) -> None:
    overrides: dict[str, object] = {
        "runtime": {"mode": "backtest"},
        "exchange": {"private_state_enabled": False},
        "replay": {
            "source_root": source,
            "start_at": start_at,
            "end_at": end_at,
        },
    }
    loaded = _load_config_or_exit(overrides=overrides)
    container = build_runtime_container(
        loaded.bootstrap,
        mode=RunMode.BACKTEST,
        source=source,
        start_at=start_at,
        end_at=end_at,
    )
    summary = asyncio.run(_run_runtime(container, summary_out=summary_out))
    typer.echo(json.dumps(summary, indent=2))


@db_app.command("upgrade")
def db_upgrade(revision: str = "head") -> None:
    loaded = _load_config_or_exit()
    run_alembic_upgrade(loaded.bootstrap.postgres_dsn, revision)
    typer.echo(f"Database upgraded to {revision}")


@db_app.command("current")
def db_current() -> None:
    loaded = _load_config_or_exit()
    run_alembic_current(loaded.bootstrap.postgres_dsn)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
