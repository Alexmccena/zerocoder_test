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
- `TB_TELEGRAM_BOT_TOKEN` (required only when `alerts.telegram.enabled=true`)

## Local commands

```bash
uv sync
uv run bot validate-config
uv run bot doctor
uv run bot db upgrade
uv run bot serve
uv run bot capture --duration-seconds 30 --public-only
uv run bot run --mode paper --duration-seconds 30
uv run bot live-preflight
uv run bot run --mode live --duration-seconds 30
uv run bot soak-paper --duration-seconds 600
uv run bot replay --source data/market_archive --speed 10
uv run bot backtest --source data/market_archive
```

## Telegram operations

`bot run --mode paper` and `bot run --mode live` start Telegram operational polling automatically when `alerts.telegram.enabled=true`.

Supported commands:

- `/status`
- `/risk`
- `/pause`
- `/resume`
- `/flatten`

Configure alert routing and command authorization in YAML:

```yaml
alerts:
  telegram:
    enabled: true
    chat_ids: [123456789]
    allowed_chat_ids: [123456789]
    allowed_user_ids: [987654321]
    min_severity: info
```

Keep the bot token in `TB_TELEGRAM_BOT_TOKEN`, not in YAML.

## SMC smoke replay

`config/dev.yaml` now enables `strategy.name: smc_scalper_v1` with reduced history sizes for local smoke runs.

The repository also includes a small replay dataset generator that produces one deterministic `open_long -> close_long` cycle on `BTCUSDT`:

```bash
docker compose up -d
uv run bot db upgrade
uv run python -m trading_bot.replay.sample_archive --output data/dev_market_archive/smc_scalper_v1_sample
uv run bot backtest --source data/dev_market_archive/smc_scalper_v1_sample --summary-out data/dev_market_archive/smc_scalper_v1_summary.json
uv run bot replay --source data/dev_market_archive/smc_scalper_v1_sample --speed 20
```

The generated archive spans `2026-03-03T00:01:00+00:00` to `2026-03-03T04:30:00+00:00` and is expected to emit exactly two strategy signals:

- `open_long`
- `close_long`

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

This repository now contains the foundation layer, phase-2 capture, phase-3 paper/replay runtime, and phase-6 live venue wiring:

- Bybit V5 REST/WS clients for public data and read-only private state
- normalized market/private events
- Redis latest cache publishers
- PostgreSQL persistence for instruments and account/private state
- Parquet market archive writer
- phase-3 paper venue, replay reader, and shared backtest runtime
- phase-6 Bybit live execution venue, startup recovery, rollout guards, and `live-preflight`

Paper soak instructions live in [docs/runbooks/paper_soak.md](docs/runbooks/paper_soak.md).
Live rollout runbooks:

- [docs/runbooks/live_testnet_smoke.md](docs/runbooks/live_testnet_smoke.md)
- [docs/runbooks/live_mainnet_micro.md](docs/runbooks/live_mainnet_micro.md)
- [docs/runbooks/live_restart_recovery.md](docs/runbooks/live_restart_recovery.md)
