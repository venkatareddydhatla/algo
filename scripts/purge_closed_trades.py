#!/usr/bin/env python3
"""Purge completed paper round-trips only — keep all open positions.

Removes entry+exit fill legs that pair into closed Trades-UI round-trips.
Does **not** delete ``paper_positions`` rows or still-open unpaired entry fills.
Use this to clear bad closed history (e.g. shared ₹996.96 exit prints) without
wiping the open book.

Usage (from repo root):

  # Preview counts only (no writes)
  python scripts/purge_closed_trades.py

  # Archive closed fill legs, then delete them
  python scripts/purge_closed_trades.py --confirm

  # Delete without copying to paper_fills_archive
  python scripts/purge_closed_trades.py --confirm --no-archive

Windows (C:\\algo, after pulling this tip):

  .\\.venv\\Scripts\\python.exe scripts\\purge_closed_trades.py
  .\\.venv\\Scripts\\python.exe scripts\\purge_closed_trades.py --confirm

Policy: docs/options-portal-paper-retention.md
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
    "packages/persistence/src",
):
    p = ROOT / rel
    if p.is_dir() and str(p) not in sys.path:
        sys.path.insert(0, str(p))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Purge closed paper round-trip fills only; keep open positions "
            "(and their unpaired entry fills)"
        )
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required to actually archive+delete closed fill legs",
    )
    parser.add_argument(
        "--mode",
        default="dry_run",
        choices=("dry_run", "demo"),
        help="Ledger mode to purge (default: dry_run)",
    )
    parser.add_argument(
        "--no-archive",
        action="store_true",
        help="Delete without copying rows to paper_fills_archive",
    )
    args = parser.parse_args()

    os.environ.setdefault("PERSISTENCE_PROFILE", "local")

    from options_portal_domain.config import paper_retention_config
    from options_portal_persistence.paper_trades import PaperTradeStore

    retention = paper_retention_config()
    store = PaperTradeStore()
    preview = store.preview_closed_purge(mode=args.mode) if store.available else {
        "available": False,
        "mode": args.mode,
        "closed_trades": 0,
        "fills_to_delete": 0,
        "fills_kept": 0,
        "open_positions": 0,
    }
    print(
        json.dumps(
            {
                "preview": {
                    **preview,
                    "retention": retention,
                    "confirm": bool(args.confirm),
                }
            },
            indent=2,
        )
    )

    if not retention.get("allow_purge_before_live", True):
        print(
            "Refusing: trading.equity.paper_retention.allow_purge_before_live is false",
            file=sys.stderr,
        )
        return 2

    if not args.confirm:
        print(
            "No changes. Re-run with --confirm to archive+purge closed round-trips "
            "only (open positions stay).",
            file=sys.stderr,
        )
        return 0

    if not store.available:
        print("Paper ledger unavailable (PERSISTENCE_PROFILE=local?)", file=sys.stderr)
        return 1

    result = store.purge_closed_trades(
        mode=args.mode,
        confirm=True,
        archive=not args.no_archive,
    )
    print(json.dumps(result, indent=2))
    if result.get("purged"):
        kept = int(result.get("open_positions_kept") or 0)
        print(
            f"Open positions kept: {kept} "
            f"({', '.join(result.get('open_symbols_kept') or []) or 'none'})",
            file=sys.stderr,
        )
    return 0 if result.get("purged") else 1


if __name__ == "__main__":
    raise SystemExit(main())
