# Gmail SMTP notifications (dry-run week)

Email alerts for equity **dry-run** paper fills and a once-daily summary.

| Event | When | Content |
|-------|------|---------|
| Fill | Each simulated paper fill persisted by the worker (entry/exit only) | Compact HTML key/value table + plain-text fallback |
| Daily summary | **16:00 IST** (once per calendar day) | HTML tables: summary fields, Open positions, Today's fills (Time IST, Side, Qty, Symbol, Price, PnL, Reason) + plain-text multipart fallback |

**Not emailed:** HOLD / neutral / overnight-hold scan ticks (console only).

**Recipient default:** `venkatareddy.dhatla@gmail.com`  
**Secrets:** never commit. Cloud agents cannot send without your local app password.

Long **Reason** strings are truncated in table cells (hover / plain appendix keeps the full text). Footer always states execution remains `dry_run`.

## Env (local `.env` only)

| Variable | Required | Default |
|----------|----------|---------|
| `GMAIL_APP_PASSWORD` | yes (to send) | — |
| `GMAIL_USER` | no | same as `NOTIFY_EMAIL_TO` |
| `NOTIFY_EMAIL_TO` | no | `venkatareddy.dhatla@gmail.com` |

Create a [Google App Password](https://myaccount.google.com/apppasswords) for the Gmail account (2FA must be on). Put the 16-character value only in `C:\algo\.env`.

Config (non-secret) under `notify` in `config/local.yaml`:

```yaml
notify:
  channels:
    - type: gmail
      enabled: true
  daily_summary:
    enabled: true
    hour_ist: 16
    minute_ist: 0
```

Dedup marker for the daily email: `data/notify_daily_summary_state.json` (gitignored under `data/`).

## Windows: pull + test emails

```powershell
cd C:\algo
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\native\stop-native.ps1
git fetch origin
git checkout cursor/gmail-summary-html-tables-42e8
git pull origin cursor/gmail-summary-html-tables-42e8
.\deploy\windows\native\setup-native.ps1
```

Edit `C:\algo\.env` — set `GMAIL_APP_PASSWORD` (and optional `GMAIL_USER` / `NOTIFY_EMAIL_TO`). Then:

```powershell
# SMTP connectivity smoke test
.\.venv\Scripts\python.exe scripts\send_test_email.py

# Force-send today's daily summary (HTML tables) without waiting for 16:00 IST.
# Does NOT write the dedup marker, so the scheduled 16:00 send can still run.
.\.venv\Scripts\python.exe scripts\send_daily_summary.py --force

# Optional: preview plain + HTML locally with no SMTP
.\.venv\Scripts\python.exe scripts\send_daily_summary.py --dry-run-print
```

Confirm inbox (and spam) for **`[dry-run] Test email`** and **`[dry-run] Daily summary …`**. Then start the worker:

```powershell
.\deploy\windows\native\start-native.ps1 -WithWorker
```

Still `dry_run` only — no live Upstox orders.

## Package

- `packages/notify` — `GmailSMTPNotifier`, `build_notifier`, `maybe_send_daily_summary`, HTML multipart formatters
- Worker: fill email in `apps/worker/app/pipeline.py`; daily check after each full scan in `apps/worker/app/main.py`
- Test CLIs: `scripts/send_test_email.py`, `scripts/send_daily_summary.py --force`
