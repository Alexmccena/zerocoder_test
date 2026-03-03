# Trading Bot Foundation

Russian overview: [readme_rus.md](readme_rus.md)

Foundation slice for a future multi-exchange trading bot. The repository now includes:

- `uv`-managed Python project with `src/` layout
- FastAPI shell for `/health`, `/ready`, `/metrics`
- Typer CLI with `serve`, `capture`, `run`, `replay`, and `backtest`
- Postgres and Redis integration points
- SQLAlchemy and Alembic foundation
- Pydantic configuration loading from YAML and environment variables

## Prerequisites

- Python 3.12+
- `uv`
- Docker and Docker Compose

## Environment

Copy `.env.example` to `.env` and adjust values if needed.

Required environment variables:

- `TB_ENV`
- `TB_CONFIG_FILE`
- `TB_POSTGRES_DSN`
- `TB_REDIS_DSN`
- `TB_LOG_LEVEL`
- `TB_HTTP_HOST`
- `TB_HTTP_PORT`
- `TB_BYBIT_API_KEY` (required only when `exchange.private_state_enabled=true`)
- `TB_BYBIT_API_SECRET` (required only when `exchange.private_state_enabled=true`)

## Local commands

```bash
uv sync
uv run bot validate-config
uv run bot doctor
uv run bot db upgrade
uv run bot serve
uv run bot capture --duration-seconds 30 --public-only
uv run bot run --mode paper --duration-seconds 30
uv run bot replay --source data/market_archive --speed 10
uv run bot backtest --source data/market_archive
```

## IDE notes

- The project uses a `src/` layout. `pyrightconfig.json` and `.vscode/settings.json` are included so the IDE can resolve the local `trading_bot` package.
- External imports such as `typer`, `fastapi`, `sqlalchemy`, `redis`, and `alembic` will remain unresolved until dependencies are installed into `.venv`.

## Docker

```bash
docker compose up --build
```

The application exposes:

- `GET /health`
- `GET /ready`
- `GET /metrics`

## Status

This repository now contains the foundation layer, phase-2 market-data capture, and the phase-3 simulated runtime:

- Bybit V5 REST/WS clients for public data and read-only private state
- normalized market/private events
- Redis latest cache publishers
- PostgreSQL persistence for instruments and account/private state
- Parquet market archive writer
- phase-3 paper venue, replay reader, and shared backtest runtime

Live Bybit execution, production strategy logic, and LLM integration are intentionally deferred.
