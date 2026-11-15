# Gmail SMTP notifications (dry-run week)

Email alerts for equity **dry-run** paper fills and a once-daily summary.

| Event | When | Content |
|-------|------|---------|
| Fill | Each simulated paper fill persisted by the worker (entry/exit only) | Symbol, side, qty, price, reason, IST time |
| Daily summary | **16:00 IST** (once per calendar day) | Open positions, today's fills, day realized P&L |

**Not emailed:** HOLD / neutral / overnight-hold scan ticks (console only).

**Recipient default:** `venkatareddy.dhatla@gmail.com`  
**Secrets:** never commit. Cloud agents cannot send without your local app password.

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

## Windows: pull + test email

```powershell
cd C:\algo
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\native\stop-native.ps1
git fetch origin
git checkout cursor/gmail-dry-run-notify-fc50
git pull origin cursor/gmail-dry-run-notify-fc50
.\deploy\windows\native\setup-native.ps1
```

Edit `C:\algo\.env` — set `GMAIL_APP_PASSWORD` (and optional `GMAIL_USER` / `NOTIFY_EMAIL_TO`). Then:

```powershell
.\.venv\Scripts\python.exe scripts\send_test_email.py
```

Confirm inbox (and spam) for **`[dry-run] Test email`**. After that, start the worker so fills + 16:00 IST summary use the same `.env`:

```powershell
.\deploy\windows\native\start-native.ps1 -WithWorker
```

## Package

- `packages/notify` — `GmailSMTPNotifier`, `build_notifier`, `maybe_send_daily_summary`
- Worker: fill email in `apps/worker/app/pipeline.py`; daily check after each full scan in `apps/worker/app/main.py`
- Test CLI: `scripts/send_test_email.py`
