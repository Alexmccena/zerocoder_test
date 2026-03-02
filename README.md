# Trading Bot Foundation

Russian overview: [readme_rus.md](readme_rus.md)

Foundation slice for a future multi-exchange trading bot. This phase provides:

- `uv`-managed Python project with `src/` layout
- FastAPI shell for `/health`, `/ready`, `/metrics`
- Typer CLI
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
uv run bot run
uv run bot capture --duration-seconds 30 --public-only
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

This repository now contains the foundation layer plus phase-2 market-data capture:

- Bybit V5 REST/WS clients for public data and read-only private state
- normalized market/private events
- Redis latest cache publishers
- PostgreSQL persistence for instruments and account/private state
- Parquet market archive writer

Strategy execution, paper trading, live order placement, and LLM integration are intentionally deferred.
