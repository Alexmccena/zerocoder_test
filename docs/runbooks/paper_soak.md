# Paper Soak Runbook

## Goal

Run the phase-5 paper runtime for 72 hours with Telegram operations enabled and confirm:

- no runaway order loop
- no lost heartbeats
- no protection-failure drift
- operational commands keep responding

## Prerequisites

- `docker compose up -d`
- `uv sync`
- `uv run bot db upgrade`
- `.env` contains `TB_TELEGRAM_BOT_TOKEN`
- config enables `alerts.telegram.enabled: true`
- `alerts.telegram.chat_ids` and `allowed_user_ids` are set

## Launch

```bash
uv run bot soak-paper --duration-seconds 259200 --summary-out data/soak/phase5_paper_summary.json
```

`soak-paper` uses the same paper runtime path as `bot run --mode paper`, but defaults to a long-lived wall-clock run.

## During the run

Check:

- `GET /health`
- `GET /ready`
- `GET /metrics`
- Telegram `/status`
- Telegram `/risk`

Operational commands:

- `/pause` blocks new open intents while existing positions remain managed
- `/resume` re-enables new open intents
- `/flatten` queues emergency flatten for all currently open positions

## Completion criteria

- process stays alive for the full soak window
- no repeated `protection_failure` alerts
- Telegram commands keep responding
- final summary JSON is written
- pytest remains green after the soak branch changes
