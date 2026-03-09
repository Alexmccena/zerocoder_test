# Костяк торгового бота

- English overview: [README.md](README.md)
- Карта кодовой базы: [docs/codebase_structure.md](docs/codebase_structure.md)
- Ранбуки: [docs/runbooks/](docs/runbooks)

Этот репозиторий — рабочий каркас торгового бота: ingestion рыночных данных, признаки, стратегии, риск, paper/replay/backtest и live-контур (Bybit + Binance Futures), плюс эксплуатация через Telegram и опциональный LLM advisory слой (OpenRouter).

## Что уже есть

- Адаптеры Bybit V5 и Binance Futures: REST + public/private WS + нормализация в доменные модели
- Market data pipeline: WS -> `MarketSnapshot` -> `FeatureSnapshot`
- `capture` режим: Parquet market archive + снимки в Postgres + latest cache в Redis
- `paper` венью + `replay` и `backtest` рантаймы
- `live` венью: отправка ордеров, private state, REST resync, rollout guards, startup recovery, `live-preflight`
- Telegram ops и LLM advisory workflows (включаются конфигом)

## Быстрый старт (локально)

Поднять зависимости и инфраструктуру:

```bash
uv sync
docker compose up -d
uv run bot db upgrade
```

Выбрать overlay-конфиг через `.env` (пример: `TB_CONFIG_FILE=config/dev.yaml` или один из `config/live_*.yaml`) и проверить:

```bash
uv run bot validate-config
uv run bot doctor
```

## Конфиги

Конфиг загружается как: `config/base.yaml` + overlay из `TB_CONFIG_FILE` + env overrides.

Часто используемые overlay-файлы:

- `config/dev.yaml`: локальная разработка (SMC стратегия, уменьшенные истории).
- `config/live_testnet.yaml`: безопасный live-testnet (`dry_run=true`, `execution_enabled=false`).
- `config/live_binance_testnet.yaml`: то же, но принудительно `exchange.primary=binance`.
- `config/live_prod_micro.yaml`: каркас micro-роллаута на mainnet (`execution_enabled=false`).
- `config/live_binance_prod_micro_fast.yaml`: Binance mainnet micro конфиг с включенной отправкой ордеров и жесткими notional лимитами (для коротких smoke-тестов).

## Переменные окружения

Минимально обязательные:

- `TB_ENV`
- `TB_CONFIG_FILE`
- `TB_POSTGRES_DSN`
- `TB_REDIS_DSN`
- `TB_LOG_LEVEL`
- `TB_HTTP_HOST`
- `TB_HTTP_PORT`

Ключи бирж нужны только когда `exchange.private_state_enabled=true`:

- `TB_BYBIT_API_KEY`, `TB_BYBIT_API_SECRET` (если `exchange.primary=bybit`)
- `TB_BINANCE_API_KEY`, `TB_BINANCE_API_SECRET` (если `exchange.primary=binance`)

Опционально:

- `TB_TELEGRAM_BOT_TOKEN` (если `alerts.telegram.enabled=true`)
- `TB_OPENROUTER_API_KEY` (если `llm.enabled=true` и `llm.provider=openrouter`)

## Команды

Paper / replay / backtest:

```bash
uv run bot run --mode paper --duration-seconds 30
uv run bot replay --source data/market_archive --speed 10
uv run bot backtest --source data/market_archive
```

Live (рекомендуемый порядок):

```bash
uv run bot live-preflight
uv run bot run --mode live --duration-seconds 300
```

Capture (архив market data):

```bash
uv run bot capture --duration-seconds 30 --public-only
```

## Важно про live

`live` режим может отправлять реальные ордера. Основные предохранители:

- `runtime.dry_run` и `live.execution_enabled`
- `exchange.testnet` и `live.allow_mainnet`
- `live.symbol_allowlist` и notional лимиты (`max_*_usdt`)
- `live.startup_recovery_policy` (`halt` или `flatten`)

Перед live запуском прогоняй `uv run bot live-preflight`.

## Статус

Реализованные “срезы”:

- Phase 2: capture pipeline (market archive + storage)
- Phase 3: paper venue + replay/backtest runtime
- Phase 6: live execution venue (Bybit + Binance Futures), startup recovery, rollout guards, `live-preflight`
- Phase 7: advisory LLM layer (OpenRouter provider, workflow queue, budgets, Telegram analyze/playbook)

