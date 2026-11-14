# Windows native (no Docker)

Run API + Vite UI directly with **Python** and **Node** when Docker Desktop fails (WSL / Virtual Machine Platform not enabled).

| Script | Purpose |
|--------|---------|
| `setup-native.ps1` | Create `.venv`, pip install, `npm install`, copy SQLite-friendly `.env` |
| `start-native.ps1` | Start uvicorn + Vite (optional `-WithWorker`) |
| `stop-native.ps1` | Stop those processes via PID files |
| `install-native-api-service.ps1` | Optional NSSM service for **API only** |
| `uninstall-native-api-service.ps1` | Remove that service |
| `_native-common.ps1` | Shared helpers (dot-source only) |

**Dry-run default.** Requires **Python 3.12.x** (reject 3.15+ unless `-AllowUnsupportedPython`). No Postgres/Redis/Docker (`PERSISTENCE_PROFILE=local` + SQLite).

Full steps: Project doc [`docs/options-portal-windows-native.md`](../../../../../docs/options-portal-windows-native.md)

Docker/NSSM path (unchanged): sibling folder [`../`](../) + [`docs/options-portal-windows-service.md`](../../../../../docs/options-portal-windows-service.md).
