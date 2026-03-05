# Live Restart Recovery

1. Stop runtime with an open protected position.
2. Start runtime again with the same live config.
3. Confirm startup completed and no `startup_recovery_halt` alert was raised.
4. Check `/status`:
   - `private_ws_connected=yes`
   - bracket is `armed`
5. If runtime halts with `startup_recovery_halt`, inspect venue state and manually restore protection before next start.
