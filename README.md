# algo — Options Portal

NSE/BSE F&O **options trading signal portal**: FastAPI API + Python worker + React/Vite UI.

**Layout:** this repository is the portal at the **repo root** (clone to `C:\algo`). There is no nested `options-portal/` folder.

**Execution default: `dry_run`.** Live Upstox order placement is blocked in v1 application code. Do not enable live trading.

Clone once from GitHub and use `git pull` for updates — do **not** keep re-copying Context/zip trees for day-to-day work.

## Clone (Windows)

```powershell
cd C:\
git clone https://github.com/venkatareddydhatla/algo.git algo
cd C:\algo
```

Requires [Git for Windows](https://git-scm.com/download/win).

## Native Windows setup (recommended — no Docker)

**Python 3.12.x is required** (binary wheels for PyYAML, etc.). Prefer `py -3.12`. Do **not** use Python 3.15 without MSVC Build Tools — `pip` will fail building PyYAML.

Also install **Node.js 20+** LTS.

```powershell
cd C:\algo
py -3.12 --version
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\native\setup-native.ps1
.\deploy\windows\native\start-native.ps1
# Optional worker: .\deploy\windows\native\start-native.ps1 -WithWorker
.\deploy\windows\native\stop-native.ps1
```

| Surface | URL |
|---------|-----|
| Web UI | http://127.0.0.1:5173 |
| API docs | http://127.0.0.1:8000/docs |
| Health | http://127.0.0.1:8000/health/ready |

All binds are **127.0.0.1** only.

- Setup creates `.venv`, installs packages (PyYAML binary wheel first), `npm install` for the UI, and copies `.env.native.example` → `.env` if missing (**never** overwrites secrets).
- Persistence: `PERSISTENCE_PROFILE=local` → SQLite under `data\` (no Postgres/Redis).
- Details: [`docs/options-portal-windows-native.md`](docs/options-portal-windows-native.md), scripts under [`deploy/windows/native/`](deploy/windows/native/).

If you previously used a zip under `C:\algo\options-portal`, switch to this clone at `C:\algo` and discard the zip copy for ongoing work.

## Docker Compose (optional)

When Docker Desktop works:

```bash
cp .env.example .env   # chmod 600 / never commit .env
# edit POSTGRES_PASSWORD / SESSION_SECRET

docker compose -f deploy/docker-compose.yml up --build
```

Windows service helpers (NSSM): [`deploy/windows/`](deploy/windows/) — see [`docs/options-portal-windows-service.md`](docs/options-portal-windows-service.md).

Do not run Docker and native stacks on the same ports at once (8000 / 5173).

## Local dev (non-Windows / without Compose)

```bash
export PERSISTENCE_PROFILE=local TRADING_MODE=dry_run
export DATABASE_URL=sqlite:///./data/options_portal.local.sqlite
export REDIS_URL=memory://local

./scripts/install_dev.sh

# Terminal A — API
export CONFIG_PATH=./config/local.yaml
cd apps/api && uvicorn app.main:app --host 127.0.0.1 --port 8000

# Terminal B — worker
cd apps/worker && python -m app.main

# Terminal C — UI
cd apps/portal-web && npm install && npm run dev
```

## Tests

```bash
./scripts/install_dev.sh
pytest -q
```

## Secure dry-run practices

1. Keep `TRADING_MODE=dry_run` and `trading.mode: dry_run` in `config/local.yaml`.
2. Never commit `.env` or real Upstox tokens — use `.env.example` / `.env.native.example` as templates.
3. Live placement is rejected by `SafeOrderRouter` + `UpstoxAdapter` in v1.
4. Kill switch: `TRADING_HALT=true`.

## Docs

| Doc | Path |
|-----|------|
| Architecture | [`docs/options-portal-architecture.md`](docs/options-portal-architecture.md) |
| Scaffold overview | [`docs/options-portal-scaffold.md`](docs/options-portal-scaffold.md) |
| Windows native | [`docs/options-portal-windows-native.md`](docs/options-portal-windows-native.md) |
| Windows Docker service | [`docs/options-portal-windows-service.md`](docs/options-portal-windows-service.md) |
| Schema / UI / interfaces | [`docs/options-portal/`](docs/options-portal/) |

## Module map

```text
apps/api            FastAPI — health, signals, news, trades, admin stubs
apps/worker         15m pipeline tick (TA → hybrid → risk → dry-run order)
apps/portal-web     React/Vite dashboard shell
apps/backtest-cli   Placeholder for offline replay
packages/domain     Types, protocols, India calendar, config loader
packages/broker-upstox  Upstox adapter (synthetic MD; OAuth TODO)
packages/ta-core    RSI/EMA/Supertrend/VWAP + confluence signal generator
packages/news-rss   RSS poller
packages/sentiment  VADER scorer + FinBERT stub hook
packages/hybrid-engine  Weighted hybrid + single-leg ATM recommender
packages/risk       Risk gates + SafeOrderRouter (live blocked)
packages/notify     Console / webhook / telegram notifiers
packages/persistence  Postgres/Redis health helpers + local SQLite profile
db/migrations       SQL schema + seed
config/             default.yaml, local.yaml, symbols.india.yaml
deploy/             docker-compose.yml + windows/ (+ native/)
docs/               Architecture + Windows guides
tests/unit          TA + hybrid + calendar + router tests
```

## Requirements

- **Native dry-run (preferred on Windows):** Python **3.12.x**, Node 20+
- **Compose:** Docker Compose (Postgres 16 + Redis 7)
- No hardcoded API credentials in git
