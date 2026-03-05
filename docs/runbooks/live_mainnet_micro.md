# Live Mainnet Micro

1. Set `TB_CONFIG_FILE=config/live_prod_micro.yaml` in `.env`.
2. Run `uv run bot validate-config`.
3. Run `uv run bot live-preflight` and confirm:
   - `"network": "mainnet"`
   - `"ws_auth_ok": true`
4. Review preflight output before launch:
   - If account has open positions/orders not managed by this bot, startup recovery can halt runtime with `startup_unprotected_position:*`.
   - Recommended: close/cleanup manual positions and stale orders before first bot start.
5. Optional dry-run on mainnet feed:
   - set `runtime.dry_run=true` in `config/live_prod_micro.yaml`
   - run `uv run bot run --mode live --duration-seconds 300`
6. Arm execution:
   - set `runtime.dry_run=false`
   - ensure `live.execution_enabled=true` and `live.allow_mainnet=true`
   - run `uv run bot run --mode live`
7. Control and monitor in Telegram (when `alerts.telegram.enabled=true`):
   - `/status`
   - `/risk`
   - `/pause`
   - `/resume`
   - `/flatten`
8. Keep micro limits (`max_order_notional_usdt`, `max_position_notional_usdt`, `max_total_exposure_usdt`) unchanged for at least 24h before any promotion.
