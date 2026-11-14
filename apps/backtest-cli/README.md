# backtest-cli

Offline simulation entrypoint (scaffold).

```bash
# TODO: implement replay against tests/fixtures/candles
python -m apps.backtest_cli run --symbols NIFTY,BANKNIFTY --from 2024-01-01 --to 2024-06-30
```

See architecture §9 for fill model and metrics.
