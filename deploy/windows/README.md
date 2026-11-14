# Windows: service / start / stop for Options Portal

## A) Docker Desktop path (this folder)

PowerShell helpers to run `deploy/docker-compose.yml` on Windows 10/11 with **Docker Desktop**.

| Script | Purpose |
|--------|---------|
| `install-service.ps1` | Install **OptionsPortal** Windows service via [NSSM](https://nssm.cc/) (manual/demand start by default) |
| `uninstall-service.ps1` | Stop stack + remove service |
| `start-portal.ps1` | `Start-Service` if installed, else `docker compose up -d --build` |
| `stop-portal.ps1` | `Stop-Service` (if any) + `docker compose down` |
| `install-scheduled-task.ps1` | Fallback Manual Scheduled Task (no NSSM) |
| `_common.ps1` | Shared helpers (dot-sourced; do not run alone) |

Full operator steps: Project doc  
[`docs/options-portal-windows-service.md`](../../../../docs/options-portal-windows-service.md)

## B) Native path (no Docker) — if VMP / WSL fails

If Docker Desktop cannot install or start (**Virtual Machine Platform** / WSL not enabled), use:

[`native/`](./native/) — Python + Node, SQLite local profile, no Postgres/Redis.

```powershell
.\deploy\windows\native\setup-native.ps1
.\deploy\windows\native\start-native.ps1
```

Doc: [`docs/options-portal-windows-native.md`](../../../../docs/options-portal-windows-native.md)

**Dry-run default.** Do not enable live trading for v1.
