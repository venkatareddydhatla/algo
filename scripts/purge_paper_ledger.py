#!/usr/bin/env python3
"""Archive and purge equity dry-run paper fills (full wipe / empty Trades page).

Also used for a **clean Day start** or **clear old Trades history**: wipe closed
+ open paper so the portal Trades list is empty (Windows: see
docs/options-portal-paper-retention.md). Before live: same full wipe when ready
for real orders. Default mode stays ``dry_run``.

Usage (from repo root, with venv / editable installs):

  # Dry-run preview (no writes)
  python scripts/purge_paper_ledger.py

  # Actually archive + purge dry_run mode ledger (full wipe)
  python scripts/purge_paper_ledger.py --confirm
  # Alias (same effect): python scripts/purge_paper_ledger.py --yes

  # Closed round-trips only (keep open positions + open entry fills)
  python scripts/purge_paper_ledger.py --confirm --closed-only
  # Prefer dedicated script: python scripts/purge_closed_trades.py --confirm

  # Open book only (keep closed round-trips)
  python scripts/purge_paper_ledger.py --confirm --open-only
  # Prefer dedicated script: python scripts/purge_open_positions.py --confirm

  # Purge without copying to paper_fills_archive
  python scripts/purge_paper_ledger.py --confirm --no-archive

Windows PowerShell (C:\\algo):

  .\\.venv\\Scripts\\python.exe scripts\\purge_paper_ledger.py
  .\\.venv\\Scripts\\python.exe scripts\\purge_paper_ledger.py --confirm

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
        description=(
            "Archive/purge entire paper ledger (open + closed) so Trades is empty; "
            "default mode dry_run"
        )
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required to actually delete fills/positions (preferred)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Alias for --confirm (kept for older tips / Day-start docs)",
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
    confirmed = bool(args.confirm or args.yes)

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
        "confirm": confirmed,
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

    if not confirmed:
        if args.closed_only:
            print(
                "No changes. Re-run with --confirm --closed-only to purge closed "
                "round-trips only (open positions stay). "
                "Or use: python scripts/purge_closed_trades.py --confirm",
                file=sys.stderr,
            )
        elif args.open_only:
            print(
                "No changes. Re-run with --confirm --open-only to clear Open "
                "positions (closed round-trips stay). "
                "Or use: python scripts/purge_open_positions.py --confirm",
                file=sys.stderr,
            )
        else:
            print(
                "No changes. Re-run with --confirm to archive+purge ALL dry_run "
                "fills and open positions (Trades page empty). "
                "--yes is an accepted alias.",
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
