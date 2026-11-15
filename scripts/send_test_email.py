#!/usr/bin/env python3
"""Send one Gmail test message for dry-run notify setup.

Run on Windows after pull (cloud agents cannot send without your app password).

Prerequisites — put these in ``C:\\algo\\.env`` (gitignored, never commit):

  GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
  # optional:
  # GMAIL_USER=venkatareddy.dhatla@gmail.com
  # NOTIFY_EMAIL_TO=venkatareddy.dhatla@gmail.com

Usage (from repo root, with native venv):

  .\\.venv\\Scripts\\python.exe scripts\\send_test_email.py

Then confirm the inbox for ``[dry-run] Test email``.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for rel in (
    "packages/domain/src",
    "packages/notify/src",
):
    p = ROOT / rel
    if p.is_dir() and str(p) not in sys.path:
        sys.path.insert(0, str(p))


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no python-dotenv dependency). Does not override existing env."""
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
        os.environ.setdefault(key, val)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send a Gmail SMTP test email (dry-run notify)"
    )
    parser.add_argument(
        "--env-file",
        default=str(ROOT / ".env"),
        help="Path to .env (default: repo root .env)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite process env with values from --env-file",
    )
    args = parser.parse_args()

    env_path = Path(args.env_file)
    if args.force and env_path.is_file():
        # Re-load forcing keys from file
        for raw in env_path.read_text(encoding="utf-8").splitlines():
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
            os.environ[key] = val
    else:
        _load_dotenv(env_path)

    from options_portal_notify.gmail import (
        DEFAULT_NOTIFY_EMAIL_TO,
        gmail_settings,
        send_gmail_message,
    )

    settings = gmail_settings(enabled=True)
    if not settings.password:
        print(
            "ERROR: GMAIL_APP_PASSWORD is not set.\n"
            f"  Add it to {env_path} (never commit).\n"
            "  Create a Google App Password for the Gmail account, then re-run.\n"
            "  Cloud agents cannot send mail without your local password.",
            file=sys.stderr,
        )
        return 1

    to_addr = settings.to_addr or DEFAULT_NOTIFY_EMAIL_TO
    subject = "[dry-run] Test email"
    body = (
        "Options Portal Gmail SMTP test.\n\n"
        f"If you received this, fill + 16:00 IST daily summary emails will work "
        f"when the worker runs with the same .env.\n\n"
        f"From: {settings.user}\n"
        f"To:   {to_addr}\n"
    )
    print(f"Sending test email to {to_addr} via smtp.gmail.com …")
    ok = send_gmail_message(subject=subject, body=body, settings=settings)
    if not ok:
        print("FAILED — check GMAIL_USER / GMAIL_APP_PASSWORD and Gmail SMTP access.", file=sys.stderr)
        return 2
    print(f"OK — check inbox (and spam) for: {subject}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
