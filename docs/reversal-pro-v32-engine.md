# Reversal Detection Pro v3.2 signal engine

Dry-run only. Tip stacked on `cursor/watchlist-open-action-3a75` (PR #59). Task ALG-78.

## What changed

| Removed | Replacement |
|---------|-------------|
| NIFTY50 Active V2 EMA13/21 + next-candle clear-of-EMA | NPR21 **Reversal Detection Pro v3.2** (Confirmed Only) |
| Multi-factor / hybrid entry gates on the Pine path | Already bypassed; remain off for entries |
| **120m / same-day re-entry cooloff** | **No cooloff** — same symbol may re-enter on the next fresh U1/D1 (or STRONG) after close |

Canonical Pine: [`pine-reversal-pro-v32.pine`](./pine-reversal-pro-v32.pine) (also `docs/pine-reversal-pro-v32.pine` in the algo repo).

## Entry / exit mapping (15m closed bars)

Defaults: **Confirmed Only**, **Medium** sensitivity, ATR length 14, method `average`, avg length 5, `confirmationBars` 0, **`require_strong: true`**.

| Signal | Action | Book |
|--------|--------|------|
| **U1** (signal flips >0) | **BUY** | Open/enter **LONG** if flat / capacity |
| **D1** (signal flips <0) | **SELL** | Open/enter **SHORT** |
| **STRONG** | Prefer / default filter | `U1 and buysignal==1` / `D1 and sellsignal==1` (EMA 9/14/21 trend aligned) |

- Preview pivots are **never** used for trading.
- Watchlist **Action** (BUY/SELL only — no HOLD) = **freshest U1/D1** (TV labels):
  1. Fresh U1 → BUY / D1 → SELL
  2. **VEDL override:** `lastConfirmedPivotIsHigh` + Trend BEARISH while ZigZag `signal` still sticky +1 → **SELL** (D1 path lag; never BUY+BEARISH after red REVERSAL / pivot high)
  3. Else ZigZag `signal` sticky (last confirmed reversal; later D1 replaces older U1)
  4. Else last confirmed pivot when `signal==0` (high→SELL / low→BUY)
  5. Else EMA Trend fallback (`buysignal`/`sellsignal`) when last unset
  6. Else `neutral_action` (default BUY)
  Worker rewrites `pine.signal.*` each scan; API reconciles stale BUY+BEARISH KV. Open overlay (PR #59, tightened ALG-83): only while paper qty>0; does not hide a fresher opposite pine Action; **Trend unchanged**.
- Watchlist **Trend** (separate column / API field): `buysignal==1` → **BULLISH**,
  `sellsignal==1` → **BEARISH**, else **NEUTRAL** (same as TV info table).
- **Entries** still require fresh U1/D1 (or STRONG) — Trend / sticky Action alone does not open trades.
- Opposite fresh **U1/D1** closes open (PR #58) — close-only; Trend does not close.
- No cooloff after close (manual, ±2%, opposite, soft flatten) — next bar’s fresh signal can re-enter.

## Kept (ops)

- `volatile_fno50` TradingView curated ~19 + custom watchlist symbols
- Full scan each 15m bar close
- `min_equity_price` ₹40, max 10 opens, entries 09:30–15:20, soft flatten ~15:28
- ±2% exits + `profit_candle_protect` + live-LTP mark guard (PR #57)
- dry_run only; no live unlock
- Manual sell/close

## Config knobs (`trading.pine_signals`)

```yaml
enabled: true
confirmation_mode: "Confirmed Only"
confirmation_bars: 0
sensitivity: Medium          # Very High … Very Low | Custom
method: average              # or high_low
atr_length: 14
average_length: 5
require_strong: true         # false → enter on raw U1/D1
action_no_hold: true         # Action never HOLD
neutral_action: BUY          # when U1/D1 + signal + trend all unset
# Custom only:
# percent_amount / rev_amount / atr_reversal
```

`require_strong: true` is the default. If live frequency is too sparse, set `require_strong: false` (documented fallback). Unit fixtures exercise both paths.

See tip `cursor/watchlist-action-trend-0791` for Action + Trend columns.

Cooloff off:

```yaml
trading.risk.entry_selection.reentry_cooloff:
  enabled: false
  minutes: 0
```

## Windows pull

```powershell
cd C:\algo
Set-ExecutionPolicy -Scope Process Bypass -Force
.\deploy\windows\native\stop-native.ps1
git fetch origin
git checkout cursor/reversal-pro-v32-c904
git pull origin cursor/reversal-pro-v32-c904
.\deploy\windows\native\setup-native.ps1
.\deploy\windows\native\start-native.ps1 -WithWorker
```

**Must stop then start** so the worker loads Reversal Pro + no-cooloff. Still `dry_run` only — no live unlock. Never paste Upstox secrets.
