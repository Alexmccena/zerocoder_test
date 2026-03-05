# LLM Advisory Runbook

## Purpose

Phase 7 adds an advisory-only LLM layer. It does not gate `ALLOW/REJECT/HALT` and does not place/cancel orders.

## Required config

1. Enable LLM in YAML (`config/base.yaml` or overlay):

```yaml
llm:
  enabled: true
  provider: openrouter
  workflows:
    pre_session_enabled: true
    periodic_enabled: true
    periodic_interval_minutes: 15
    post_trade_enabled: true
    risk_halt_enabled: true
```

2. Set environment variables:

- `TB_OPENROUTER_API_KEY`
- optional: `TB_OPENROUTER_BASE_URL`, `TB_OPENROUTER_HTTP_REFERER`, `TB_OPENROUTER_APP_NAME`

## Runtime behavior

- `pre_session` runs on startup.
- `periodic` runs by configured cadence.
- `post_trade` runs on position close.
- `risk_halt` runs on risk halt events.
- All workflow tasks are processed through an async queue and never block market ingestion/execution.

## Telegram operations

- `/analyze <prompt>`: ad-hoc analysis with current status/risk context.
- `/playbook set <text|json>`: save day playbook.
- `/playbook show`: show active playbook.
- `/playbook clear`: clear active playbook.

## Budget guard

When limits are exceeded (`calls/tokens/cost`), LLM calls are skipped and a warning alert is emitted. Trading continues.

Metrics:

- `tb_llm_requests_total`
- `tb_llm_request_seconds`
- `tb_llm_tokens_total`
- `tb_llm_cost_usd_total`
- `tb_llm_budget_block_total`
- `tb_llm_parse_fail_total`

## Storage

All advisory events are written to `llm_advice` with:

- `advice_type`
- `model_name`
- `input_hash`
- `output_json`
- `run_session_id`
- `symbol`
