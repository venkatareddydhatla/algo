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

### Pull a feature branch (Top hybrid strip + Backtest)

When checking out work that adds `packages/backtest` or UI routes, reinstall editables and restart:

```powershell
cd C:\algo
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\native\stop-native.ps1
git fetch origin
git checkout cursor/top-hybrid-backtest-2198
git pull origin cursor/top-hybrid-backtest-2198
# Re-run setup so packages\backtest + tzdata are installed
.\deploy\windows\native\setup-native.ps1
.\deploy\windows\native\start-native.ps1 -WithWorker
```

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
