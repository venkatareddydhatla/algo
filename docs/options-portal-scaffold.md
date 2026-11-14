# Options Portal — v1 code scaffold

User-facing pointer to the runnable monorepo scaffold.

## Code location

**Root:** this GitHub repository (clone to `C:\algo`).

Full README (module map, security, implemented vs stubbed):  
[`README.md`](../README.md)

Architecture plan this scaffold follows:  
[`options-portal-architecture.md`](./options-portal-architecture.md)

## How to start (dry-run)

```bash
cd C:\algo   # or your clone of this repo
cp .env.example .env
chmod 600 .env
# set POSTGRES_PASSWORD (and optional secrets) — do not commit .env

docker compose -f deploy/docker-compose.yml up --build
```

- **UI:** http://127.0.0.1:5173  
- **API:** http://127.0.0.1:8000/docs  
- **Health:** http://127.0.0.1:8000/health/ready  

`TRADING_MODE` defaults to **dry_run**. Live Upstox order placement is **blocked** in v1 application code even if admin UI shows a live switch.

### Windows service (Docker Desktop)

On Windows 10/11, install a **manual** service that runs the same Compose file (NSSM; download once — not bundled). Full steps: [`options-portal-windows-service.md`](./options-portal-windows-service.md).

**Prerequisites:** Docker Desktop **running**; elevated PowerShell for install/uninstall.

```powershell
cd C:\algo
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\install-service.ps1 -DownloadNssm   # one-time; or pass -NssmPath

.\deploy\windows\start-portal.ps1    # or: Start-Service OptionsPortal
.\deploy\windows\stop-portal.ps1     # or: Stop-Service OptionsPortal
```

- Creates `.env` from `.env.example` if missing (never overwrites secrets).
- Default start type: **demand/manual** (not auto unless `-AutoStart`).
- URLs: http://127.0.0.1:5173 (UI), http://127.0.0.1:8000/docs (API).
- Fallback: `install-scheduled-task.ps1` or plain Compose via `start-portal.ps1` when no service is installed.

Scripts live under [`deploy/windows/`](../deploy/windows/).

### Windows native (no Docker) — if VMP / WSL fails

When Docker Desktop cannot enable **Virtual Machine Platform** / WSL, run API + Vite with Python + Node and SQLite:

```powershell
cd C:\algo
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\native\setup-native.ps1
.\deploy\windows\native\start-native.ps1
.\deploy\windows\native\stop-native.ps1
```

Requires **Python 3.12.x** (avoid 3.15 — PyYAML wheels / MSVC) and **Node 20+**. No Postgres/Redis.  
Doc: [`options-portal-windows-native.md`](./options-portal-windows-native.md). Prefer `git clone` / `git pull` over zip re-copies.

### Dev without Compose

```bash
./scripts/install_dev.sh
# Native-friendly env (optional):
#   export PERSISTENCE_PROFILE=local TRADING_MODE=dry_run
#   export DATABASE_URL=sqlite:///./data/options_portal.local.sqlite
# API
cd apps/api && uvicorn app.main:app --host 127.0.0.1 --port 8000
# UI (mock fallback if API down)
cd apps/portal-web && npm install && npm run dev
# tests
pytest -q
```

## What you get

| Piece | Notes |
|-------|--------|
| FastAPI + worker + React/Vite | Python-only backend; portal-web UI shell |
| Postgres 16 + Redis 7 | Compose services; SQL migrations under `db/migrations/` |
| Plugins | Broker (Upstox dry-run), TA, RSS, VADER, hybrid engine, risk, notify |
| Options | Single-leg ATM / nearest liquid; NIFTY, BANKNIFTY, RELIANCE, TCS |
| Sentiment | VADER on; FinBERT stub hook only |
| Safety | No hardcoded credentials; live placement gated |

## Still stubbed / needs credentials

- Upstox OAuth, live market data, and live orders (synthetic candles/chain used locally)
- Full DB persistence for signals/news (API serves mock reads for UI)
- FinBERT sidecar (optional later)
- Backtest CLI (placeholder)

See the code README for the detailed module map.
