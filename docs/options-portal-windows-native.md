# Options Portal — Windows native (no Docker)

Run the API + web UI on **Windows 10/11** with **Python** and **Node** only. Use this when **Docker Desktop** fails (for example Virtual Machine Platform / WSL not enabled).

**Code:** [`deploy/windows/native/`](../deploy/windows/native/)

Execution remains **`dry_run`**. Live Upstox placement is blocked in v1. No Postgres or Redis required for first boot (`PERSISTENCE_PROFILE=local` + SQLite file under `data\`).

---

## Prerequisites (install once)

| Tool | Version | Notes |
|------|---------|--------|
| Python | **3.12.x (required for typical setup)** | [python.org](https://www.python.org/downloads/) — enable **Add python.exe to PATH**. Confirm with `py -3.12 --version`. |
| Node.js | **20+** LTS | [nodejs.org](https://nodejs.org/) (includes npm) |

**Do not use Python 3.15** (or other very new releases) for native setup unless you install Microsoft Visual C++ Build Tools. On 3.15, packages like **PyYAML** often have **no binary wheel**, so `pip` tries to compile and fails with `Failed building wheel for pyyaml` / `Microsoft Visual C++ 14.0 is required`. Python **3.13+** may also lack wheels for some deps — prefer **3.12**.

`setup-native.ps1` rejects Python **3.15+** unless you pass `-AllowUnsupportedPython`. It also pre-installs a **binary** PyYAML wheel before editable installs.

Administrator is **not** required for setup/start/stop. Optional NSSM API service install does need Admin.

---

## Exact commands (`C:\algo`)

```powershell
cd C:\algo
Set-ExecutionPolicy -Scope Process Bypass

# One-time: venv + pip + npm + .env (SQLite defaults)
.\deploy\windows\native\setup-native.ps1

# Start API (uvicorn) + Vite UI
.\deploy\windows\native\start-native.ps1

# Optional: also start the worker tick loop
# .\deploy\windows\native\start-native.ps1 -WithWorker

# Stop
.\deploy\windows\native\stop-native.ps1
```

### Pull a feature branch (equity dry-run auto-orders)

When checking out work that adds paper equity fills / Trades ledger, reinstall editables and restart:

```powershell
cd C:\algo
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\native\stop-native.ps1
git fetch origin
git checkout cursor/equity-dry-run-orders-c815
git pull origin cursor/equity-dry-run-orders-c815
# Re-run setup so editable packages pick up paper ledger + equity planner
.\deploy\windows\native\setup-native.ps1
.\deploy\windows\native\start-native.ps1 -WithWorker
```

Verify: http://127.0.0.1:5173/trades shows paper fills (side/qty/price/IST/reason/dry_run); http://127.0.0.1:8000/api/v1/health has `instrument=equity` and `execution=dry_run`. No live Upstox orders are placed.

### Pull soft EOD + market sentiment (stacked)

Soft flatten window **15:20–15:40 IST** (close paper equity unless strong same-side `|hybrid|` overnight conviction). After **15:40** the worker **refuses** new soft_flatten exits (missed window / late restart) — not a hard force square-off. Open positions are evaluated on a **dedicated open-book poll** (~15s; `SOFT_FLATTEN_POLL_SEC`) so soft flatten is not blocked behind a multi-minute full F&O scan. Session close stays **15:30** NSE. Optional light NIFTY/BANKNIFTY gate blocks new *opposite* entries only when index hybrid strongly conflicts.

```powershell
cd C:\algo
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\native\stop-native.ps1
git fetch origin
git checkout cursor/soft-eod-market-sentiment-bc84
git pull origin cursor/soft-eod-market-sentiment-bc84
.\deploy\windows\native\setup-native.ps1
.\deploy\windows\native\start-native.ps1 -WithWorker
```

Config knobs (non-secret): `trading.session.soft_flatten_ist`, `soft_flatten_until_ist`, `soft_flatten_poll_interval_sec`, `overnight_hold_min_hybrid_abs`, `trading.market_sentiment.enabled`. Worker logs flatten / overnight-hold / refused-after-until / index-block reasons. Still `dry_run` only.

### Pull News scroll + UPDATED date/time + retention (stacked)

Signals **Updated** shows **date + time IST** (`YYYY-MM-DD HH:MM IST`). News page has a vertical scrollbar for long lists. Live News UI = **today IST only**; prior days archive into SQLite history until **quarter-end** (not shown on the News page). Policy: [`docs/options-portal-news-retention.md`](./options-portal-news-retention.md).

```powershell
cd C:\algo
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\native\stop-native.ps1
git fetch origin
git checkout cursor/news-scroll-archive-updated-d7a5
git pull origin cursor/news-scroll-archive-updated-d7a5
.\deploy\windows\native\setup-native.ps1
.\deploy\windows\native\start-native.ps1 -WithWorker
```

Verify: Signals Updated column has a date (not time-only); News table scrolls vertically; `/api/v1/news` returns `scope: today_ist`. Still `dry_run` only.

### Pull max open symbols = 5 (stacked)

Equity dry-run risk default **`max_open_symbols: 5`** (was 4). Caps concurrent open equity symbols; exits remain allowed when the book is full. Still `dry_run` only — no live orders.

```powershell
cd C:\algo
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\native\stop-native.ps1
git fetch origin
git checkout cursor/max-open-symbols-five-beac
git pull origin cursor/max-open-symbols-five-beac
.\deploy\windows\native\setup-native.ps1
.\deploy\windows\native\start-native.ps1 -WithWorker
```

Verify: `config\local.yaml` (and `default.yaml`) show `risk.max_open_symbols: 5`. Still `dry_run` only.

### Pull max open symbols = 10 (stacked)

Equity dry-run risk default **`max_open_symbols: 10`** (was 5). Caps concurrent open equity symbols so more names can be assessed during dry-run week; exits remain allowed when the book is full. Override via `risk.max_open_symbols` in `config\local.yaml`. Still `dry_run` only — no live orders.

```powershell
cd C:\algo
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\native\stop-native.ps1
git fetch origin
git checkout cursor/max-open-symbols-ten-e41e
git pull origin cursor/max-open-symbols-ten-e41e
.\deploy\windows\native\setup-native.ps1
.\deploy\windows\native\start-native.ps1 -WithWorker
```

Verify: `config\local.yaml` (and `default.yaml`) show `risk.max_open_symbols: 10`. Still `dry_run` only.

### Pull fix shared exit LTP (stacked)

Fixes dry-run exits that all showed the same Exit px (e.g. ₹996.96 from 360ONE) across BPCL/AXISBANK/etc. Each symbol’s paper exit now uses **that** symbol’s Upstox LTP (matched by instrument key / tradingsymbol), with candle close only as a per-symbol fallback. Agents cannot access your Upstox account — correctness is verified via per-instrument key + mocked LTP responses. **Old bad fills stay in the SQLite ledger** until new exits; they are not rewritten.

```powershell
cd C:\algo
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\native\stop-native.ps1
git fetch origin
git checkout cursor/fix-shared-exit-ltp-b1b7
git pull origin cursor/fix-shared-exit-ltp-b1b7
.\deploy\windows\native\setup-native.ps1
.\deploy\windows\native\start-native.ps1 -WithWorker
```

Verify: next soft_flatten / signal exits show **different** Exit px per symbol when LTPs differ. Still `dry_run` only.

### Pull purge closed trades (keep opens) (stacked)

Clears **closed** paper round-trip history only (including old bad ₹996.96 exits — desired). **Open positions stay.** Prefer `scripts\purge_closed_trades.py --confirm` after pulling this tip. Policy: [`docs/options-portal-paper-retention.md`](./options-portal-paper-retention.md).

```powershell
cd C:\algo
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\native\stop-native.ps1
git fetch origin
git checkout cursor/purge-closed-trades-2133
git pull origin cursor/purge-closed-trades-2133
.\deploy\windows\native\setup-native.ps1
.\deploy\windows\native\start-native.ps1 -WithWorker

# Preview, then purge closed fills only (open book kept)
.\.venv\Scripts\python.exe scripts\purge_closed_trades.py
.\.venv\Scripts\python.exe scripts\purge_closed_trades.py --confirm
```

Verify: http://127.0.0.1:5173/trades — Closed trades empty / cleared; Open positions still listed. Bad shared 996.96 exits are gone. Still `dry_run` only.

### Pull fix IOC/360ONE entry+exit marks (stacked)

IOC (and ~170 other F&O names) had **no** Upstox `instrument_key`, so entry/exit fell back to synthetic candles. After the shared-base fix, IOC exits printed ~₹3958 (IOC’s hash synthetic) while entries could still show ₹996.96 from the older shared path / wrong 360ONE ISIN. This tip maps **every** F&O equity (including IOC) to the correct `NSE_EQ|<ISIN>`, fixes stale 360ONE/KOTAKBANK/BAJFINANCE keys, logs `symbol+instrument_key+price source` on every mark, and refuses paper fills on synthetic marks when `UPSTOX_ACCESS_TOKEN` is set.

```powershell
cd C:\algo
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\native\stop-native.ps1
git fetch origin
git checkout cursor/fix-ioc-entry-exit-marks-12ab
git pull origin cursor/fix-ioc-entry-exit-marks-12ab
.\deploy\windows\native\setup-native.ps1
.\deploy\windows\native\start-native.ps1 -WithWorker

# Clear bad closed history again (open positions kept)
.\.venv\Scripts\python.exe scripts\purge_closed_trades.py --confirm
```

Verify: worker logs show `IOC LTP: source=upstox instrument_key=NSE_EQ|INE242A01010 price=…`; new IOC/360ONE fills use **that** symbol’s LTP (not 996.96 / 3958). Still `dry_run` only. **Purge closed trades again after this pull** so old contaminated rows disappear.

### Pull paper / dry-run trade retention (stacked)

Keep **all** dry-run paper fills through the review week (no auto-delete). Trades UI shows **closed round-trips** (paginated; not today-only) — same entry/exit pairing as Backtest Fills and the daily summary email. Before real orders, purge/archive explicitly via admin API or `scripts\purge_paper_ledger.py`. After live, prefer ≥90 days fill retention. Policy: [`docs/options-portal-paper-retention.md`](./options-portal-paper-retention.md).

```powershell
cd C:\algo
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\native\stop-native.ps1
git fetch origin
git checkout cursor/dry-run-trade-retention-e83e
git pull origin cursor/dry-run-trade-retention-e83e
.\deploy\windows\native\setup-native.ps1
.\deploy\windows\native\start-native.ps1 -WithWorker
```

Verify: http://127.0.0.1:5173/trades shows fill pager + history note; `config\local.yaml` has `trading.equity.paper_retention.auto_purge: false`. **Do not** run purge during dry-run week. When starting live later:

```powershell
.\.venv\Scripts\python.exe scripts\purge_paper_ledger.py
.\.venv\Scripts\python.exe scripts\purge_paper_ledger.py --yes
```

Still `dry_run` only — no live orders.

### Pull Gmail SMTP notify (stacked)

Email on each dry-run paper fill + daily summary at **16:00 IST** (NSE **trading days only** — no Sat/Sun/stub-holiday fill or summary emails; soft-flatten / paper orders also skipped). The worker polls the schedule on its own loop (~30s; not after each full-universe scan). Recipient default `venkatareddy.dhatla@gmail.com` (HTML tables: Open positions + Today's trades as Backtest-style round-trips — Symbol, Side LONG/SHORT, Entry/Exit IST+px, PnL, Reason — + plain-text fallback). Needs a Google **App Password** in local `.env` (`GMAIL_APP_PASSWORD`). Optional: `GMAIL_USER`, `NOTIFY_EMAIL_TO`. Never commit secrets. Cloud agents cannot send without your password — test on Windows after pull (`--force` works any day). Policy: [`docs/options-portal-gmail-notify.md`](./options-portal-gmail-notify.md).

```powershell
cd C:\algo
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\native\stop-native.ps1
git fetch origin
git checkout cursor/summary-round-trip-trades-6717
git pull origin cursor/summary-round-trip-trades-6717
.\deploy\windows\native\setup-native.ps1
# Edit C:\algo\.env — set GMAIL_APP_PASSWORD (never commit / paste in chat)
.\.venv\Scripts\python.exe scripts\send_test_email.py
.\.venv\Scripts\python.exe scripts\send_daily_summary.py --force
# Confirm inbox for [dry-run] Test email + [dry-run] Daily summary, then:
.\deploy\windows\native\start-native.ps1 -WithWorker
```

Still `dry_run` only — no live orders.

### Pull Trades page round-trip closed trades (stacked on summary PR)

Trades & P&L **Closed trades** table matches Backtest / daily summary (one row per entry+exit). Shared pairing: `options_portal_domain.round_trips`.

```powershell
cd C:\algo
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\native\stop-native.ps1
git fetch origin
git checkout cursor/trades-page-round-trips-cb5e
git pull origin cursor/trades-page-round-trips-cb5e
.\deploy\windows\native\setup-native.ps1
.\deploy\windows\native\start-native.ps1 -WithWorker
```

Open http://127.0.0.1:5173/trades — Open positions unchanged; Closed trades show Symbol, Side LONG/SHORT, Entry/Exit IST, Entry/Exit px, PnL, Reason, dry_run.

### Pull price floor + 120m cooloff + profit candle protect (stacked) — tip

Three Venkata rules on the manual-sell tip: (1) skip score/entry when LTP or last close **&lt; ₹20**; (2) **120m** same-IST-day cooloff after any exit (incl. manual) before re-trading the symbol; (3) **profit_candle_protect** — in-profit long whose latest 15m opens below prior mid and closes below prior low → immediate close (short symmetric). Keeps Pine clear-EMA, **±2%**, **09:30**, max open **10**, manual Sell/Close. Stacked on `cursor/manual-sell-open-ae11` (PR #50).

```powershell
cd C:\algo
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\native\stop-native.ps1
git fetch origin
git checkout cursor/price-cooloff-profit-candle-548e
git pull origin cursor/price-cooloff-profit-candle-548e
.\deploy\windows\native\setup-native.ps1
.\deploy\windows\native\start-native.ps1 -WithWorker
```

Confirm `config/local.yaml`: `min_equity_price: 20`, `reentry_cooloff.minutes: 120`, `profit_candle_protect.enabled: true`, `fixed_pct_exit`, `entries_open: "09:30"`, `max_open_symbols: 10`, `dry_run`. Worker open-book log may show `profit_candle=profit_candle_protect` or `price_floor` skips. After merge into the tip chain / `main`, pull that branch instead.

### Pull manual Sell/Close on open trades (stacked)

Trades → Open positions: **Sell/Close** (long) or **Cover** (short) closes that dry_run paper leg immediately via `POST /api/v1/trades/{symbol}/close` with `confirm=true` + `reason=manual_close`. Confirm dialog; dry_run only (no live broker). Pine path honors **reentry_cooloff** after exits (incl. manual_close) so the worker does not instantly re-open the same symbol. Prefer the price/cooloff/profit-candle tip above. Stacked on `cursor/strict-candle-clear-emas-d84e` (PR #49).

**Must stop then start** so API picks up the new route (start-native skips a still-running uvicorn).

```powershell
cd C:\algo
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\native\stop-native.ps1
git fetch origin
git checkout cursor/manual-sell-open-ae11
git pull origin cursor/manual-sell-open-ae11
.\deploy\windows\native\setup-native.ps1
.\deploy\windows\native\start-native.ps1 -WithWorker
```

Confirm route exists: http://127.0.0.1:8000/docs → `POST /api/v1/trades/{symbol}/close`. Open http://127.0.0.1:5173/trades — Sell/Close (or Cover) → confirm → row leaves Open; Closed shows `manual_close`. Red error toast if API/CORS fails. Still `dry_run`, **09:30** entry gate, open-book **±2%**, max open **10**, cooloff 120m.

### Pull strict candle-clear EMA confirm (stacked)

After EMA cross, require the **next completed 15m candle** fully clear of both EMAs: buy when `low > max(EMA)`; sell when `high < min(EMA)`. No fire if wick touches/sits on the lines. Prefer closed bar. Keeps **09:30** entry gate, open-book **±2%**, max open **10**, stocks-only, `dry_run`. Prefer the manual-close tip above. Stacked on `cursor/purge-930-entry-gate-5e77` (PR #48).

```powershell
cd C:\algo
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\native\stop-native.ps1
git fetch origin
git checkout cursor/strict-candle-clear-emas-d84e
git pull origin cursor/strict-candle-clear-emas-d84e
.\deploy\windows\native\setup-native.ps1
.\deploy\windows\native\start-native.ps1 -WithWorker
```

Confirm worker log: `clear_above=True` / `clear_below=True` on Pine BUY/SELL; touch cases stay HOLD. Still `entries_open: "09:30"`, `fixed_pct_exit`, `max_open_symbols: 10`, `dry_run`.

### Pull Day start purge + 09:30 entry gate (stacked)

Clean Day start: full Windows purge of paper closed+open so the portal is empty for today. **No new entries before 09:30 IST** (first 15m 09:15–09:30 must complete); entry window **09:30–15:20**. Open-book **±2%** exits still run from cash open **09:15**. Prefer the candle-clear tip above. Stacked on `cursor/fixed-2pct-exits-d814` (PR #47).

```powershell
cd C:\algo
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\native\stop-native.ps1
git fetch origin
git checkout cursor/purge-930-entry-gate-5e77
git pull origin cursor/purge-930-entry-gate-5e77
.\deploy\windows\native\setup-native.ps1

# Clean Day start — preview then wipe all paper fills + opens
.\.venv\Scripts\python.exe scripts\purge_paper_ledger.py
.\.venv\Scripts\python.exe scripts\purge_paper_ledger.py --yes

.\deploy\windows\native\start-native.ps1 -WithWorker
```

Confirm `config/local.yaml`: `trading.session.entries_open: "09:30"`, `no_new_entries_after: "15:20"`, `open: "09:15"`. Worker before 09:30: `outside_entry_session: before entries_open_ist=09:30` while open-book ticks may still show `fixed_pct=…`. Still `dry_run` only.

### Pull fixed ±2% open exits (stacked)

Open equity dry-run only: exit at **+2%** profit or **−2%** stop from entry (`pct_profit_2` / `pct_stop_2`). Not an entry filter. ATR trail + morning dynamic TP **off** as primary; soft EOD flatten stays. Pine buy/sell entry + stocks-only + `dry_run` unchanged. **max_open_symbols / capacity ceiling = 10**. Prefer the 09:30 gate tip above. Stacked on `cursor/pine-next-candle-confirm-99ae` (PR #46).

```powershell
cd C:\algo
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\native\stop-native.ps1
git fetch origin
git checkout cursor/fixed-2pct-exits-d814
git pull origin cursor/fixed-2pct-exits-d814
.\deploy\windows\native\setup-native.ps1
.\deploy\windows\native\start-native.ps1 -WithWorker
```

Verify worker log: `Open-book management loop starting ... fixed_pct=True atr_trail=False` and open-book ticks with `fixed_pct=pct_profit_2|pct_stop_2|none`. Confirm `config/local.yaml` has `trading.risk.fixed_pct_exit.enabled: true`, `max_open_symbols: 10`, ATR/morning `enabled: false`. Still `dry_run` only.

### Pull |hybrid| ranked entry selection (stacked)

Equity dry-run **new entries** fill free `max_open_symbols` slots by strongest `|hybrid|` (not CSV/scan first-fill). **Anti-churn:** keep existing opens — only fill empty slots (no rotate for tiny score edges). Soft flatten / overnight `|hybrid| ≥ 0.70` unchanged. Cap stays **10** (configurable).

```powershell
cd C:\algo
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\native\stop-native.ps1
git fetch origin
git checkout cursor/hybrid-rank-entry-selection-ecf7
git pull origin cursor/hybrid-rank-entry-selection-ecf7
.\deploy\windows\native\setup-native.ps1
.\deploy\windows\native\start-native.ps1 -WithWorker
```

Verify worker log: `Entry selection enabled anti_churn=empty_slots_only` and after a scan `Entry selection flush`. Still `dry_run` only.

### Pull dynamic open-symbol capacity (stacked)

Equity dry-run **open count is dynamic by `|hybrid|` strength**: hard ceiling **15**, floor **4**, admit only candidates with `|hybrid| ≥ 0.55`, fill empty slots strongest-first. **Anti-churn** stays `empty_slots_only`. Soft flatten / overnight `|hybrid| ≥ 0.70` unchanged. Manage knobs in `trading.risk` / `entry_selection.dynamic_capacity`.

```powershell
cd C:\algo
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\native\stop-native.ps1
git fetch origin
git checkout cursor/dynamic-open-symbol-capacity-53b7
git pull origin cursor/dynamic-open-symbol-capacity-53b7
.\deploy\windows\native\setup-native.ps1
.\deploy\windows\native\start-native.ps1 -WithWorker
```

Verify worker log: `Entry selection enabled ... dynamic=True floor=4 ceiling=15` and `Entry selection flush`. Still `dry_run` only.

### Pull fewer high-conviction entries (stacked)

Day2 brokerage churn fix: raise admit floor **`|hybrid| ≥ 0.65`** (after **12:00 IST → 0.70**), hard ceiling **8**, **90 min** same-session re-entry cooloff after any close. Anti-churn stays `empty_slots_only`. Soft flatten / ATR trail / morning window / index block unchanged. Tip parent: `cursor/atr-trail-scale-out-c110`.

```powershell
cd C:\algo
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\native\stop-native.ps1
git fetch origin
git checkout cursor/fewer-high-conviction-entries-4334
git pull origin cursor/fewer-high-conviction-entries-4334
.\deploy\windows\native\setup-native.ps1
.\deploy\windows\native\start-native.ps1 -WithWorker
```

Verify worker log: `Entry selection enabled ... ceiling=8 min_|hybrid|=0.65 cooloff=True/90m afternoon_after=12:00` and flush lines with `cooloff_blocked`. Still `dry_run` only.

### Pull ease entry hurdles (stacked) — tip

Day4 morning still empty: soften proximity / RVOL / news without a trade quota. Proximity **0.1×ATR**, **skip until 10:30 IST**; RVOL **≥1.0×**; news **fail-open** (hard block only on clear conflict). Keeps `|h|` floors / MTF / ATR>1% / cooloff 120m / max 6 / soft flatten 15:28 / adaptive / dry_run from tip #43. Stacked on `cursor/relax-afternoon-floors-9f4f` (PR #43).

```powershell
cd C:\algo
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\native\stop-native.ps1
git fetch origin
git checkout cursor/ease-entry-hurdles-ec02
git pull origin cursor/ease-entry-hurdles-ec02
.\deploy\windows\native\setup-native.ps1
.\deploy\windows\native\start-native.ps1 -WithWorker
```

Verify worker log: `proximity_until=10:30 proximity=0.1×ATR rvol=1.0× news_fail_open=True` and per-symbol `proximity_until=…` / `news_fail_open=…` factor details. Still `dry_run` only.

### Pull high-conviction multi-factor gate (stacked)

Venkata exact multi-factor equity F&O stock gate (indices excluded). Supersedes looser dual-session entry floors: entries **09:15–15:20**; `|h|` **0.75 / 0.80 / 0.85** (before 12:00 / after 12:00 / after 14:20); MTF 15m+1h; RVOL 1.5×/20; ATR>1.2%; proximity 0.2×ATR; news 1h conflict; cooloff **120m** after any trade; max open **6** (4 if WR&lt;50% over last 20 trips); soft flatten **15:28** / overnight **0.85+trend**; adaptive ±0.05 clamp [0.70,0.90]. Prefer the ease-entry tip above. Stacked on `cursor/dual-session-high-conviction-2224` (PR #41).

```powershell
cd C:\algo
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\native\stop-native.ps1
git fetch origin
git checkout cursor/high-conviction-multi-factor-99e6
git pull origin cursor/high-conviction-multi-factor-99e6
.\deploy\windows\native\setup-native.ps1
.\deploy\windows\native\start-native.ps1 -WithWorker
```

Verify worker log: `high_conviction=True floors=0.75/0.80/0.85 late_after=14:20`, `cooloff=True/120m`, `ceiling=6`, `no_new_entries_after=15:20`, and `SKIP`/`ENTRY` factor lines. Still `dry_run` only.

### Pull dual-session high conviction (stacked)

Day3 still too chatty: morning scalp churn from low TP/stops, not from needing a tiny open-cap. Tip: **morning 09:15–10:00** `|hybrid| ≥ 0.75` + **max 3 new** (not refill); morning TP/stop **max(1.0%, 1.5×ATR)** / **−max(1.0%, 1.25×ATR)**; **rest of day** until **11:30** `|h| ≥ 0.72`, cooloff **180m**, ceiling **12** (floor 3); ATR stop **−2.0×**, trail act **+1.5×** / dist **1.2×**, profit_book **+2.5×** (or +1.2%). Soft flatten / stocks-only / after-hours gates unchanged. Prefer the multi-factor tip above. Stacked on `cursor/stricter-fewer-trades-day3-ffcc` (PR #40).

```powershell
cd C:\algo
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\native\stop-native.ps1
git fetch origin
git checkout cursor/dual-session-high-conviction-2224
git pull origin cursor/dual-session-high-conviction-2224
.\deploy\windows\native\setup-native.ps1
.\deploy\windows\native\start-native.ps1 -WithWorker
```

Verify worker log: `Entry selection enabled ... ceiling=12 min_|hybrid|=0.72 cooloff=True/180m morning=True until=10:00 morning_min=0.75 morning_max_new=3` and session `no_new_entries_after=11:30`. Still `dry_run` only.

### Pull stricter fewer trades Day3 (stacked)

Day3 still too many morning round-trips. Tip: **`|hybrid| ≥ 0.72`** (after **12:00 → 0.75**), hard ceiling **5**, **180 min** cooloff, **no new entries after 10:30 IST**, morning TP **max(0.6% entry, 1.0×ATR)**. Prefer the dual-session tip above. Stacked on `cursor/after-hours-risk-exits-1f31` (PR #39).

```powershell
cd C:\algo
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\native\stop-native.ps1
git fetch origin
git checkout cursor/stricter-fewer-trades-day3-ffcc
git pull origin cursor/stricter-fewer-trades-day3-ffcc
.\deploy\windows\native\setup-native.ps1
.\deploy\windows\native\start-native.ps1 -WithWorker
```

Verify worker log: `Entry selection enabled ... ceiling=5 min_|hybrid|=0.72 cooloff=True/180m` and session `no_new_entries_after=10:30`. Still `dry_run` only.

### Pull after-hours entry gate (stacked)

**New equity entries** (open/add / flip_open + entry Gmail) only on NSE trading days inside the configured entry window (`trading.session.open` → `no_new_entries_after`; dual-session tip **`[09:15, 11:30)` IST**). Nights, pre-open, and post-cutoff no longer persist Action entry fills. Soft flatten / risk exits / overnight hold and news/TA scans are unchanged. Shared helper: `is_trading_session` in `session_gates`.

```powershell
cd C:\algo
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\native\stop-native.ps1
git fetch origin
git checkout cursor/after-hours-entry-gate-383b
git pull origin cursor/after-hours-entry-gate-383b
.\deploy\windows\native\setup-native.ps1
.\deploy\windows\native\start-native.ps1 -WithWorker
```

**Tonight’s bogus opens** (e.g. MUTHOOTFIN SHORT @ ~00:01 IST): pull the **purge open positions** tip below to clear the Open list while keeping 16 Nov closed rows. `purge_closed_trades.py` does **not** remove still-open entries; full `purge_paper_ledger.py --yes` wipes closed history too. Still `dry_run` only.

### Pull purge open positions (keep closed history) (stacked)

Clears **Open positions** + unpaired entry fills (after-hours bogus opens) while **keeping closed round-trips** (e.g. 16 Nov completed Trades rows). Opposite of `purge_closed_trades.py`.

```powershell
cd C:\algo
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\native\stop-native.ps1
git fetch origin
git checkout cursor/purge-open-positions-383b
git pull origin cursor/purge-open-positions-383b
.\deploy\windows\native\setup-native.ps1

# Preview, then clear Open book (closed 16 Nov rows stay)
.\.venv\Scripts\python.exe scripts\purge_open_positions.py
.\.venv\Scripts\python.exe scripts\purge_open_positions.py --confirm

.\deploy\windows\native\start-native.ps1 -WithWorker
```

Verify: http://127.0.0.1:5173/trades — Open positions empty; Closed trades still show 16 Nov rows. Still `dry_run` only.

If the API fails with `ZoneInfoNotFoundError: 'No time zone found with key Asia/Kolkata'`, install tzdata and restart:

```powershell
cd C:\algo
.\.venv\Scripts\python.exe -m pip install "tzdata>=2024.1"
# or re-run setup-native.ps1 (installs tzdata)
.\deploy\windows\native\setup-native.ps1
.\deploy\windows\native\start-native.ps1 -WithWorker
```

Signals page: Top CE / Top PE strip above the table. Nav **Backtest** runs `/api/v1/backtest/run` (always `dry_run`). Prefers **Upstox historical candles**; default Run **refuses** silent synthetic so win-rate/PnL cannot look like expectancy. Missing history → API/UI error (`No real history for SYMBOL; check Upstox token / instrument / dates`). Optional advanced `allow_synthetic` labels metrics **synthetic — not tradeable**.

| Surface | URL |
|---------|-----|
| Web UI | http://127.0.0.1:5173 |
| API docs | http://127.0.0.1:8000/docs |
| Health | http://127.0.0.1:8000/health/ready |

All binds are **127.0.0.1** only.

---

## If you hit `Failed building wheel for pyyaml` / MSVC 14.0

You are almost certainly on **Python 3.15** (or another version without wheels). Fix without Build Tools:

1. Install **Python 3.12.x** from [python.org](https://www.python.org/downloads/) (check **Add python.exe to PATH**).
2. From the repo root, remove the old venv and re-run setup:

```powershell
cd C:\algo
Remove-Item -Recurse -Force .venv
# ensure py -3.12 works
py -3.12 --version
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\native\setup-native.ps1
.\deploy\windows\native\start-native.ps1
```

`setup-native.ps1` prefers `py -3.12` when the launcher is available. You do **not** need Visual C++ Build Tools on Python 3.12 for this stack.

---

## What setup does

1. Creates `.venv` under the repo root (using Python 3.12 when available).
2. Upgrades pip, then installs **PyYAML from a binary wheel** (`--only-binary=:all:`).
3. `pip install -e` for packages + `apps/api` + `apps/worker`.
4. `npm install` in `apps\portal-web`.
5. Copies `.env.native.example` → `.env` if `.env` is missing (never overwrites secrets).
6. Ensures `data\` for `options_portal.local.sqlite`.

`.env` native defaults include:

- `PERSISTENCE_PROFILE=local`
- `DATABASE_URL=sqlite:///./data/options_portal.local.sqlite`
- `REDIS_URL=memory://local` (in-process stub; no Redis server)
- `TRADING_MODE=dry_run`

---

## Optional: NSSM service for API only

Day-to-day prefer `start-native.ps1` (API + UI). To run **API only** as a Manual Windows service:

```powershell
# Elevated PowerShell
.\deploy\windows\native\install-native-api-service.ps1 -DownloadNssm
Start-Service OptionsPortalApiNative
```

Still start the UI with `start-native.ps1` (or `cd apps\portal-web; npm run dev`). Uninstall: `uninstall-native-api-service.ps1`.

---

## Docker path still available

If Docker Desktop works on your PC, you can use the Compose/NSSM scripts instead:

[`options-portal-windows-service.md`](./options-portal-windows-service.md)

Do not run Docker and native stacks on the same ports at once (both use 8000 / 5173).

---

## Safety

- Keep `TRADING_MODE=dry_run`.
- Do not commit `.env`.
- Upstox keys are optional for dry-run (synthetic market data).
- These scripts do **not** claim the portal is running on your PC until **you** run them locally.

---

## Live Upstox market data (candles) — still dry_run orders

To score TA on **live candles** while keeping **orders dry_run**, add env var **names** below to `C:\algo\.env` (gitignored). Put values only there — never in git, chat logs, or PR text.

| Variable | Purpose |
|----------|---------|
| `UPSTOX_API_KEY` | App key from Upstox developer console |
| `UPSTOX_API_SECRET` | App secret |
| `UPSTOX_ACCESS_TOKEN` | Bearer token for Historical Candle V3 / quotes |
| `UPSTOX_REDIRECT_URI` | OAuth redirect (default `http://127.0.0.1:8000/auth/upstox/callback`) — used if you later refresh via `UPSTOX_REFRESH_TOKEN` |
| `UPSTOX_REFRESH_TOKEN` | Optional; non-interactive refresh when access token expires |
| `UPSTOX_INSTRUMENT_MAP` | Optional JSON `{"SYMBOL":"NSE_EQ\|ISIN"}` overrides |

```powershell
cd C:\algo
# Edit C:\algo\.env — set the UPSTOX_* names above (values never committed)
.\deploy\windows\native\stop-native.ps1
.\deploy\windows\native\start-native.ps1 -WithWorker
```

Check Health: http://127.0.0.1:5173/health or http://127.0.0.1:8000/api/v1/health — `market_data_source` should be `upstox` when the token is present (or `synthetic` without it / on fetch failure).

News stays RSS. Orders stay `dry_run` (live placement blocked). If credentials were ever pasted into chat, **rotate** them in the Upstox developer console after wiring.
