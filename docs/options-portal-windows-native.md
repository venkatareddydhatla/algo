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

Soft flatten at **15:20 IST** (close paper equity unless strong same-side `|hybrid|` overnight conviction). Session close stays **15:30** NSE. User monitoring note: **09:15–15:40 IST** — no hard force square-off at 15:40. Optional light NIFTY/BANKNIFTY gate blocks new *opposite* entries only when index hybrid strongly conflicts.

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

Config knobs (non-secret): `trading.session.soft_flatten_ist`, `overnight_hold_min_hybrid_abs`, `trading.market_sentiment.enabled`. Worker logs flatten / overnight-hold / index-block reasons. Still `dry_run` only.

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

Email on each dry-run paper fill + daily summary at **16:00 IST** (NSE **trading days only** — no Sat/Sun/stub-holiday fill or summary emails; soft-flatten / paper orders also skipped). Recipient default `venkatareddy.dhatla@gmail.com` (HTML tables: Open positions + Today's trades as Backtest-style round-trips — Symbol, Side LONG/SHORT, Entry/Exit IST+px, PnL, Reason — + plain-text fallback). Needs a Google **App Password** in local `.env` (`GMAIL_APP_PASSWORD`). Optional: `GMAIL_USER`, `NOTIFY_EMAIL_TO`. Never commit secrets. Cloud agents cannot send without your password — test on Windows after pull (`--force` works any day). Policy: [`docs/options-portal-gmail-notify.md`](./options-portal-gmail-notify.md).

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

If the API fails with `ZoneInfoNotFoundError: 'No time zone found with key Asia/Kolkata'`, install tzdata and restart:

```powershell
cd C:\algo
.\.venv\Scripts\python.exe -m pip install "tzdata>=2024.1"
# or re-run setup-native.ps1 (installs tzdata)
.\deploy\windows\native\setup-native.ps1
.\deploy\windows\native\start-native.ps1 -WithWorker
```

Signals page: Top CE / Top PE strip above the table. Nav **Backtest** runs `/api/v1/backtest/run` (always `dry_run`).

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
