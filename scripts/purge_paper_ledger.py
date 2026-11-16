#!/usr/bin/env python3
"""Archive and purge equity dry-run paper fills before starting live orders.

Dry-run week: do **not** run this — keep all paper fills for review.
Only run when you are ready to clean the ledger before real orders.

Usage (from repo root, with venv / editable installs):

  # Dry-run preview (no writes)
  python scripts/purge_paper_ledger.py

  # Actually archive + purge dry_run mode ledger
  python scripts/purge_paper_ledger.py --yes

  # Closed round-trips only (keep open positions + open entry fills)
  python scripts/purge_paper_ledger.py --yes --closed-only
  # Prefer dedicated script: python scripts/purge_closed_trades.py --confirm

  # Open book only (keep closed round-trips)
  python scripts/purge_paper_ledger.py --yes --open-only
  # Prefer dedicated script: python scripts/purge_open_positions.py --confirm

  # Purge without copying to paper_fills_archive
  python scripts/purge_paper_ledger.py --yes --no-archive

Config gate: trading.equity.paper_retention.allow_purge_before_live (default true).
Policy: docs/options-portal-paper-retention.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Allow running without installed editable packages when PYTHONPATH is set by
# Windows native / pytest; otherwise prefer repo packages via cwd.
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
        description="Archive/purge paper ledger before live (not during dry-run week)"
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Required to actually delete fills/positions",
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
    parser.add_argument(
        "--keep-positions",
        action="store_true",
        help="Delete fills only; leave open paper_positions rows",
    )
    parser.add_argument(
        "--closed-only",
        action="store_true",
        help=(
            "Delete completed round-trip fill legs only; keep open positions "
            "and unpaired open entry fills (same as scripts/purge_closed_trades.py)"
        ),
    )
    parser.add_argument(
        "--open-only",
        action="store_true",
        help=(
            "Delete open positions + unpaired entry fills; keep closed "
            "round-trips (same as scripts/purge_open_positions.py)"
        ),
    )
    args = parser.parse_args()

    # Prefer local SQLite profile for native Windows / dry-run
    os.environ.setdefault("PERSISTENCE_PROFILE", "local")

    from options_portal_domain.config import paper_retention_config
    from options_portal_persistence.paper_trades import PaperTradeStore

    if args.closed_only and args.open_only:
        print("Refusing: --closed-only and --open-only are mutually exclusive", file=sys.stderr)
        return 2

    retention = paper_retention_config()
    store = PaperTradeStore()
    counts: dict = {
        "available": store.available,
        "fills": store.count_fills(mode=args.mode) if store.available else 0,
        "open_positions": len(store.list_positions(mode=args.mode)) if store.available else 0,
        "closed_only": bool(args.closed_only),
        "open_only": bool(args.open_only),
        "retention": retention,
        "confirm": bool(args.yes),
    }
    if args.closed_only and store.available:
        counts.update(store.preview_closed_purge(mode=args.mode))
    if args.open_only and store.available:
        counts.update(store.preview_open_purge(mode=args.mode))
    print(json.dumps({"preview": counts}, indent=2))

    if not retention.get("allow_purge_before_live", True):
        print(
            "Refusing: trading.equity.paper_retention.allow_purge_before_live is false",
            file=sys.stderr,
        )
        return 2

    if not args.yes:
        if args.closed_only:
            print(
                "No changes. Re-run with --yes --closed-only to purge closed "
                "round-trips only (open positions stay). "
                "Or use: python scripts/purge_closed_trades.py --confirm",
                file=sys.stderr,
            )
        elif args.open_only:
            print(
                "No changes. Re-run with --yes --open-only to clear Open "
                "positions (closed round-trips stay). "
                "Or use: python scripts/purge_open_positions.py --confirm",
                file=sys.stderr,
            )
        else:
            print(
                "No changes. Re-run with --yes to archive+purge "
                "(only when starting live — keep dry-run week history).",
                file=sys.stderr,
            )
        return 0

    if not store.available:
        print("Paper ledger unavailable (PERSISTENCE_PROFILE=local?)", file=sys.stderr)
        return 1

    result = store.archive_and_purge(
        mode=args.mode,
        confirm=True,
        archive=not args.no_archive,
        clear_positions=False if args.closed_only else not args.keep_positions,
        closed_only=bool(args.closed_only),
        open_only=bool(args.open_only),
    )
    print(json.dumps(result, indent=2))
    return 0 if result.get("purged") else 1


if __name__ == "__main__":
    raise SystemExit(main())
