from __future__ import annotations

import asyncio
import json
from typing import Protocol

import typer
import uvicorn

from trading_bot.app import create_app
from trading_bot.bootstrap.container import build_container
from trading_bot.bootstrap.settings import BootstrapSettings
from trading_bot.config.loader import load_app_config
from trading_bot.domain.enums import ServiceStatus
from trading_bot.domain.models import HealthReport
from trading_bot.observability.metrics import AppMetrics
from trading_bot.storage.db import run_alembic_current, run_alembic_upgrade


app = typer.Typer(help="Trading bot foundation CLI.")
db_app = typer.Typer(help="Database commands.")
app.add_typer(db_app, name="db")


class DoctorContainer(Protocol):
    async def doctor_report(self) -> HealthReport: ...

    async def shutdown(self) -> None: ...


def _load_config_or_exit() -> tuple[BootstrapSettings, str]:
    metrics = AppMetrics()
    try:
        bootstrap = BootstrapSettings()
        loaded = load_app_config(bootstrap)
    except Exception as exc:  # pragma: no cover - exercised via CLI tests
        metrics.record_config_validation_failure()
        typer.echo(f"Configuration error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    return bootstrap, loaded.fingerprint


async def _doctor(container: DoctorContainer) -> str:
    try:
        report = await container.doctor_report()
        return json.dumps(report.model_dump(mode="json"), indent=2)
    finally:
        shutdown = getattr(container, "shutdown", None)
        if shutdown is not None:
            await shutdown()


@app.command("validate-config")
def validate_config() -> None:
    _, fingerprint = _load_config_or_exit()
    typer.echo(f"Config valid. fingerprint={fingerprint}")


@app.command()
def doctor() -> None:
    bootstrap, _ = _load_config_or_exit()
    container = build_container(bootstrap)
    payload = asyncio.run(_doctor(container))
    typer.echo(payload)
    if json.loads(payload)["status"] != ServiceStatus.OK.value:
        raise typer.Exit(code=1)


@app.command()
def run(
    host: str | None = typer.Option(default=None, help="Override configured HTTP host."),
    port: int | None = typer.Option(default=None, help="Override configured HTTP port."),
) -> None:
    bootstrap, _ = _load_config_or_exit()
    container = build_container(bootstrap)
    uvicorn.run(
        create_app(container),
        host=host or container.config.observability.http_host,
        port=port or container.config.observability.http_port,
    )


@db_app.command("upgrade")
def db_upgrade(revision: str = "head") -> None:
    bootstrap, _ = _load_config_or_exit()
    run_alembic_upgrade(bootstrap.postgres_dsn, revision)
    typer.echo(f"Database upgraded to {revision}")


@db_app.command("current")
def db_current() -> None:
    bootstrap, _ = _load_config_or_exit()
    run_alembic_current(bootstrap.postgres_dsn)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
