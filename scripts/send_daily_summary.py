#!/usr/bin/env python3
"""Force-send (or preview) the dry-run daily summary email locally.

Use on Windows after pull to test the HTML table layout without waiting for
16:00 IST. Cloud agents cannot send without your Gmail app password.

Prerequisites — put these in ``C:\\algo\\.env`` (gitignored, never commit):

  GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
  # optional:
  # GMAIL_USER=venkatareddy.dhatla@gmail.com
  # NOTIFY_EMAIL_TO=venkatareddy.dhatla@gmail.com

Usage (from repo root, with native venv):

  # Send one daily summary now (does NOT mark the day — 16:00 IST still runs)
  .\\.venv\\Scripts\\python.exe scripts\\send_daily_summary.py --force

  # Preview status / body shape without SMTP (no password needed)
  .\\.venv\\Scripts\\python.exe scripts\\send_daily_summary.py --dry-run-print

  # Send and also write data/notify_daily_summary_state.json (skips scheduled send)
  .\\.venv\\Scripts\\python.exe scripts\\send_daily_summary.py --force --mark-sent
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for rel in (
    "packages/domain/src",
    "packages/notify/src",
    "packages/persistence/src",
):
    p = ROOT / rel
    if p.is_dir() and str(p) not in sys.path:
        sys.path.insert(0, str(p))


def _load_dotenv(path: Path, *, force: bool = False) -> None:
    """Minimal .env loader (no python-dotenv dependency)."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        eq = line.find("=")
        if eq < 1:
            continue
        key = line[:eq].strip()
        val = line[eq + 1 :].strip()
        if (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            val = val[1:-1]
        if force:
            os.environ[key] = val
        else:
            os.environ.setdefault(key, val)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send or preview the dry-run daily summary email (local only)"
    )
    parser.add_argument(
        "--env-file",
        default=str(ROOT / ".env"),
        help="Path to .env (default: repo root .env)",
    )
    parser.add_argument(
        "--force-env",
        action="store_true",
        help="Overwrite process env with values from --env-file",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass 16:00 IST / once-per-day / non-trading-day gates and send now",
    )
    parser.add_argument(
        "--mark-sent",
        action="store_true",
        help="After a successful --force send, write the daily dedup marker "
        "(default: do not mark so the scheduled 16:00 email still sends)",
    )
    parser.add_argument(
        "--dry-run-print",
        action="store_true",
        help="Print formatted plain + HTML sample from paper store; do not SMTP",
    )
    args = parser.parse_args()

    env_path = Path(args.env_file)
    _load_dotenv(env_path, force=args.force_env)
    os.environ.setdefault("PERSISTENCE_PROFILE", "local")
    os.environ.setdefault("CONFIG_PATH", str(ROOT / "config" / "local.yaml"))

    from options_portal_domain.config import load_config
    from options_portal_domain.tz import now_ist
    from options_portal_notify.daily_summary import maybe_send_daily_summary
    from options_portal_notify.gmail import format_daily_summary_email, gmail_settings

    cfg = load_config()

    if args.dry_run_print:
        mode = "dry_run"
        positions: list = []
        recent_fills: list = []
        day_realized = 0.0
        try:
            from options_portal_persistence.paper_trades import PaperTradeStore

            store = PaperTradeStore()
            if store.available:
                snap = store.portfolio_snapshot(mode=mode)
                positions = list(snap.get("positions") or [])
                day_realized = float(snap.get("day_realized_pnl") or 0.0)
                # Full recent history for entry/exit pairing (not day-filtered)
                recent_fills = store.list_fills(mode=mode, limit=200, offset=0)
        except Exception as exc:  # noqa: BLE001
            print(f"(paper store unavailable: {exc})", file=sys.stderr)

        day = now_ist().date().isoformat()
        subject, plain, html_body = format_daily_summary_email(
            day_ist=day,
            day_realized=day_realized,
            positions=positions,
            fills=recent_fills,
            mode=mode,
        )
        print(f"Subject: {subject}")
        print("--- plain ---")
        print(plain)
        print("--- html (first 2k chars) ---")
        print(html_body[:2000])
        if len(html_body) > 2000:
            print(f"... ({len(html_body)} chars total)")
        print(
            "\n(No email sent. Use --force on Windows with GMAIL_APP_PASSWORD to SMTP.)"
        )
        return 0

    if not args.force:
        print(
            "Refusing to send without --force (avoids accidental SMTP).\n"
            "  Preview:  python scripts/send_daily_summary.py --dry-run-print\n"
            "  Send now: python scripts/send_daily_summary.py --force",
            file=sys.stderr,
        )
        return 1

    settings = gmail_settings(enabled=True)
    if not settings.password:
        print(
            "ERROR: GMAIL_APP_PASSWORD is not set.\n"
            f"  Add it to {env_path} (never commit).\n"
            "  Cloud agents cannot send mail without your local password.\n"
            "  Preview without SMTP: python scripts/send_daily_summary.py --dry-run-print",
            file=sys.stderr,
        )
        return 1

    print(
        f"Force-sending daily summary to {settings.to_addr} "
        f"(mark_sent={args.mark_sent}) …"
    )
    status = maybe_send_daily_summary(
        config=cfg,
        force=True,
        mark_sent=args.mark_sent,
    )
    print(json.dumps(status, indent=2, default=str))
    if not status.get("sent"):
        print(
            f"FAILED — reason={status.get('reason')}. "
            "Check GMAIL_APP_PASSWORD / SMTP access.",
            file=sys.stderr,
        )
        return 2
    print("OK — check inbox for [dry-run] Daily summary …")
    if not status.get("marked_sent"):
        print("(Dedup marker not written — scheduled 16:00 IST send can still run.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
