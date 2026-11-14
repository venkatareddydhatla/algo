# Options Portal — Windows service (Docker Compose)

Run the local stack on **Windows 10/11** with **Docker Desktop**, either as a Windows service (NSSM) or with on-demand PowerShell wrappers.

**Code:** [`deploy/windows/`](../deploy/windows/)  
**Compose:** [`deploy/docker-compose.yml`](../deploy/docker-compose.yml)

Execution remains **`dry_run`** by default. Live Upstox placement is blocked in v1 application code.

> **Docker install blocked?** If Virtual Machine Platform / WSL cannot be enabled, use the native path instead: [`options-portal-windows-native.md`](./options-portal-windows-native.md) (`deploy/windows/native/`).

---

## Prerequisites

1. **Docker Desktop** installed and **running** (whale icon idle / engine ready).
2. This repo tree on disk (WorkingDirectory = options-portal root).
3. **PowerShell as Administrator** for `install-service.ps1` / `uninstall-service.ps1`.
4. [NSSM](https://nssm.cc/download) **2.24** (recommended) — **not shipped** in the repo (license/clarity). Either:
   - Pass `-DownloadNssm` on install (fetches `https://nssm.cc/release/nssm-2.24.zip` into `deploy\windows\tools\`), or
   - Download manually from https://nssm.cc/download, extract `win64\nssm.exe` to  
     `deploy\windows\tools\nssm\win64\nssm.exe`, or
   - Pass `-NssmPath 'C:\path\to\nssm.exe'`.

Review NSSM terms on https://nssm.cc/ before downloading.

---

## One-time install

```powershell
cd C:\algo
Set-ExecutionPolicy -Scope Process Bypass

# Creates .env from .env.example if missing (never overwrites an existing .env)
.\deploy\windows\install-service.ps1 -DownloadNssm
```

- Service name: **`OptionsPortal`**
- Start type: **Manual / demand** (default). Use `-AutoStart` only if you want Automatic after boot (Docker Desktop must already be up).
- App: `docker.exe` with `compose -f <repo>\deploy\docker-compose.yml up`
- WorkingDirectory: **repo root**
- Logs: `deploy\windows\logs\service-stdout.log` / `service-stderr.log`

Optional auto-start:

```powershell
.\deploy\windows\install-service.ps1 -DownloadNssm -AutoStart
```

---

## Start / stop on demand

```powershell
# Prefer scripts (service if installed, else compose up/down)
.\deploy\windows\start-portal.ps1
.\deploy\windows\stop-portal.ps1

# Or native service cmdlets (elevated if needed)
Start-Service OptionsPortal
Stop-Service OptionsPortal
Get-Service OptionsPortal
```

| Surface | URL |
|---------|-----|
| Web UI | http://127.0.0.1:5173 |
| API docs | http://127.0.0.1:8000/docs |
| Health | http://127.0.0.1:8000/health/ready |

All binds are **127.0.0.1** only.

---

## Uninstall

```powershell
.\deploy\windows\uninstall-service.ps1
# optional: also drop the Postgres volume
.\deploy\windows\uninstall-service.ps1 -RemoveVolumes
```

`.env` is left in place so secrets are not deleted.

---

## Fallback if service install is awkward

1. **Plain Compose** (no service): `start-portal.ps1` / `stop-portal.ps1` already fall back to `docker compose up -d --build` / `down` when the service is missing.
2. **Scheduled Task** (manual):

```powershell
.\deploy\windows\install-scheduled-task.ps1
Start-ScheduledTask -TaskPath '\OptionsPortal\' -TaskName 'StartStack'
```

Prefer the NSSM service when you want Start-Service / Stop-Service integration.

---

## Safety notes

- Keep `TRADING_MODE=dry_run` in `.env` and `trading.mode: dry_run` in `config/local.yaml`.
- Do not commit `.env`. Change `POSTGRES_PASSWORD` / `SESSION_SECRET` after first copy from `.env.example`.
- Starting Docker Desktop and starting this service are separate steps; the service will fail if the Docker engine is not ready.
- These scripts **do not** claim or prove the service is running on your PC until **you** run them locally.
