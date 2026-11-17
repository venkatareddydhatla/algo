# Paper / dry-run trade retention

Policy for the equity **paper ledger** (`paper_fills` / `paper_positions`) under
`PERSISTENCE_PROFILE=local` (SQLite). Distinct from [news retention](./options-portal-news-retention.md).

## Summary

| Phase | What happens to fills / positions |
|-------|-----------------------------------|
| **Dry-run week** | **No auto-delete.** Keep all simulated fills and open positions for review. Closing a position removes the open row only — fill history stays. |
| **Before real orders** | **Explicit purge/archive** only (API or script + config flag). Clean the ledger when you decide to start live — not during the review week. |
| **After live** | Prefer retain fills **≥ 90 days** for analysis (`live_min_retain_days`). Auto-retention never runs in `dry_run` / `demo`. |

Trades UI / `GET /api/v1/trades` show **closed round-trips** (paginated entry+exit rows: Symbol, Side LONG/SHORT, Entry/Exit IST+px, PnL, Reason — same pairing as Backtest Fills / daily summary). Open legs stay under Open positions. Raw fill legs remain in the SQLite ledger (`raw_fills_total`); history is not today-only.

## Config

```yaml
trading:
  equity:
    paper_retention:
      auto_purge: false              # never auto-delete during dry_run
      allow_purge_before_live: true  # gate for explicit purge path
      live_min_retain_days: 90       # floor after live (≥90)
```

`auto_purge: false` is the dry-run default. Do not enable scheduled deletion of
paper fills while reviewing the dry-run week.

## Purge / archive before live

### API

```http
POST /api/v1/admin/paper-ledger/purge
Content-Type: application/json

{
  "confirm": true,
  "archive": true,
  "clear_positions": true,
  "mode": "dry_run"
}
```

- Requires `confirm: true` and `allow_purge_before_live: true`.
- With `archive: true`, rows are copied to `paper_fills_archive` then removed from `paper_fills`.
- Does **not** unlock live Upstox placement (still gated in v1).

### Script (Windows native / CLI)

From repo root (`C:\algo` or Linux):

```powershell
# Preview counts only
.\.venv\Scripts\python.exe scripts\purge_paper_ledger.py

# Archive + purge when starting live
.\.venv\Scripts\python.exe scripts\purge_paper_ledger.py --yes
```

```bash
python scripts/purge_paper_ledger.py          # preview
python scripts/purge_paper_ledger.py --yes    # archive + purge
```

## Live retention (≥90 days)

`PaperTradeStore.run_live_retention(mode="live", min_retain_days=90)` deletes
fills older than the cutoff **only** when `mode` is not `dry_run`/`demo`.
Callers must pass live mode; dry-run always skips. Config floor is 90 days.

## Windows native

Same SQLite file under `data\`. No extra services. See
[options-portal-windows-native.md](./options-portal-windows-native.md).
