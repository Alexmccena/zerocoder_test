# Trading Bot Foundation

- Russian overview: [readme_rus.md](readme_rus.md)
- Codebase map: [docs/codebase_structure.md](docs/codebase_structure.md)
- Runbooks: [docs/runbooks/](docs/runbooks)

Foundation slice for a multi-exchange trading bot. The repository includes:

- Bybit V5 and Binance Futures adapters (REST + public/private WS + normalizers)
- Market data ingestion (WS) + optional REST polling (open interest / funding)
- Capture runtime: Parquet market archive + Postgres snapshots + Redis latest cache
- Paper venue + replay/backtest runtimes
- Live execution venue with rollout guards, startup recovery, and `live-preflight`
- Telegram ops and optional LLM advisory layer (OpenRouter)

## Prerequisites

- Python 3.12+
- `uv`
- Docker and Docker Compose (recommended for Postgres/Redis)

## Environment

Copy `.env.example` to `.env` and adjust values if needed.

Minimal required environment variables:

- `TB_ENV`
- `TB_CONFIG_FILE`
- `TB_POSTGRES_DSN`
- `TB_REDIS_DSN`
- `TB_LOG_LEVEL`
- `TB_HTTP_HOST`
- `TB_HTTP_PORT`

Exchange credentials are required only when `exchange.private_state_enabled=true`:

- `TB_BYBIT_API_KEY` and `TB_BYBIT_API_SECRET` (when `exchange.primary=bybit`)
- `TB_BINANCE_API_KEY` and `TB_BINANCE_API_SECRET` (when `exchange.primary=binance`)

Optional:

- `TB_TELEGRAM_BOT_TOKEN` (only when `alerts.telegram.enabled=true`)
- `TB_OPENROUTER_API_KEY` (only when `llm.enabled=true` and `llm.provider=openrouter`)
- `TB_OPENROUTER_BASE_URL` (optional override, default `https://openrouter.ai/api/v1`)
- `TB_OPENROUTER_HTTP_REFERER` (optional)
- `TB_OPENROUTER_APP_NAME` (optional)

## Configs

Config is loaded as: `config/base.yaml` + overlay from `TB_CONFIG_FILE` + environment overrides.

Common overlays:

- `config/dev.yaml`: local development defaults (SMC strategy enabled, small histories).
- `config/live_testnet.yaml`: live runtime on testnet in safe mode (`dry_run=true`, `execution_enabled=false`).
- `config/live_binance_testnet.yaml`: same, but forces `exchange.primary=binance`.
- `config/live_prod_micro.yaml`: mainnet micro rollout scaffold (`execution_enabled=false`; `exchange.primary` stays whatever `base.yaml` sets unless overridden).
- `config/live_binance_prod_micro_fast.yaml`: Binance mainnet micro config with execution enabled and tight notionals (intended for short smoke tests).

## Local commands

```bash
uv sync
docker compose up -d
uv run bot validate-config
uv run bot doctor
uv run bot db upgrade
uv run bot serve

# Capture (market archive)
uv run bot capture --duration-seconds 30 --public-only

# Paper / replay / backtest
uv run bot run --mode paper --duration-seconds 30
uv run bot soak-paper --duration-seconds 600
uv run bot live-preflight
uv run bot run --mode live --duration-seconds 300
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
- `/analyze <prompt>`
- `/playbook set <text|json>`
- `/playbook show`
- `/playbook clear`

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

Implemented slices:

- Phase 2: capture pipeline (market archive + storage)
- Phase 3: paper venue + replay/backtest runtime
- Phase 6: live execution venue with Bybit and Binance Futures adapters, rollout guards, startup recovery, and `live-preflight`
- Phase 7: advisory LLM layer (OpenRouter provider, workflow queue, budgets, Telegram analyze/playbook)

Runbooks:

- Paper soak: [docs/runbooks/paper_soak.md](docs/runbooks/paper_soak.md)
- Live rollout: [docs/runbooks/live_testnet_smoke.md](docs/runbooks/live_testnet_smoke.md), [docs/runbooks/live_mainnet_micro.md](docs/runbooks/live_mainnet_micro.md), [docs/runbooks/live_restart_recovery.md](docs/runbooks/live_restart_recovery.md)
- LLM advisory: [docs/runbooks/llm_advisory.md](docs/runbooks/llm_advisory.md)
