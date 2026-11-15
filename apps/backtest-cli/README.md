# backtest-cli

Offline dry-run hybrid signal replay. Engine lives in `packages/backtest`
(`options_portal_backtest`); this folder is the documented entrypoint.

```bash
# From repo root (editable install of packages/backtest)
python -m options_portal_backtest run --symbol NIFTY --bars 200
python -m options_portal_backtest run --symbol BANKNIFTY --from 2024-01-01 --to 2024-06-30 --json

# Or via API (always dry_run)
curl -s "http://127.0.0.1:8000/api/v1/backtest/run?symbol=NIFTY&bars=200"
```

Candles: optional CSV under `tests/fixtures/candles/<SYMBOL>.csv`
(`ts,symbol,open,high,low,close,volume`). If missing, a deterministic synthetic
15m series is generated.

Outputs: trades, win rate, spot-proxy PnL, max drawdown, profit factor.
Keep `dry_run` — live fills are not supported in v1.

See architecture §9 for the intended fill model and metrics.
