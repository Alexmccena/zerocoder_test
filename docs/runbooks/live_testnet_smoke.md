# Live Testnet Smoke

1. Set `TB_CONFIG_FILE=config/live_testnet.yaml`.
2. Run `uv run bot validate-config`.
3. Run `uv run bot live-preflight` and verify `ws_auth_ok=true`.
4. Start dry-run runtime: set `runtime.dry_run=true` and run `uv run bot run --mode live --duration-seconds 300`.
5. Arm execution manually (`live.execution_enabled=true`) and repeat a short run.
6. Verify `/status`, `/risk`, `/pause`, `/resume`, `/flatten` in Telegram.
