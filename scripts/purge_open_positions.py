#!/usr/bin/env python3
"""Clear the open paper book; keep completed round-trip history.

Deletes all ``paper_positions`` rows and unpaired open entry fills (e.g.
after-hours MUTHOOTFIN) while retaining closed entry+exit legs already in
Trades history (e.g. 16 Nov 2026 completed rows).

Opposite of ``scripts/purge_closed_trades.py`` (that keeps opens, drops closed).
Full wipe: ``scripts/purge_paper_ledger.py --yes`` (deletes everything).

Usage (from repo root):

  # Preview counts only (no writes)
  python scripts/purge_open_positions.py

  # Archive open legs, then delete them — closed round-trips stay
  python scripts/purge_open_positions.py --confirm

  # Delete without copying to paper_fills_archive
  python scripts/purge_open_positions.py --confirm --no-archive

Windows (C:\\algo, after pulling tip):

  .\\.venv\\Scripts\\python.exe scripts\\purge_open_positions.py
  .\\.venv\\Scripts\\python.exe scripts\\purge_open_positions.py --confirm

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
            "Purge open paper positions + unpaired entry fills; "
            "keep closed round-trip history"
        )
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required to actually archive+delete open-book rows",
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
        help="Delete without copying to paper_fills_archive",
    )
    args = parser.parse_args()

    os.environ.setdefault("PERSISTENCE_PROFILE", "local")

    from options_portal_domain.config import paper_retention_config
    from options_portal_persistence.paper_trades import PaperTradeStore

    retention = paper_retention_config()
    store = PaperTradeStore()
    preview = (
        store.preview_open_purge(mode=args.mode)
        if store.available
        else {
            "available": False,
            "mode": args.mode,
            "closed_trades_kept": 0,
            "fills_to_delete": 0,
            "fills_kept": 0,
            "open_positions": 0,
        }
    )
    print(
        json.dumps(
            {
                "preview": preview,
                "retention": retention,
                "confirm": bool(args.confirm),
                "open_only": True,
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
            "No changes. Re-run with --confirm to clear Open positions "
            "(closed round-trips stay). "
            "Opposite of purge_closed_trades.py.",
            file=sys.stderr,
        )
        return 0

    if not store.available:
        print("Paper ledger unavailable (PERSISTENCE_PROFILE=local?)", file=sys.stderr)
        return 1

    result = store.purge_open_positions(
        mode=args.mode,
        confirm=True,
        archive=not args.no_archive,
    )
    print(json.dumps(result, indent=2))
    return 0 if result.get("purged") else 1


if __name__ == "__main__":
    raise SystemExit(main())
