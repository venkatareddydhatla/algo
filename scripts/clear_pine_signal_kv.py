#!/usr/bin/env python3
"""One-shot: delete pine.signal.* KV rows so GET /signals rebuilds from candles.

Usage (Windows native, after stop, from C:\\algo):
  .\\.venv\\Scripts\\python.exe scripts\\clear_pine_signal_kv.py
  .\\.venv\\Scripts\\python.exe scripts\\clear_pine_signal_kv.py --symbol BHARTIARTL
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for rel in (
    "packages/domain/src",
    "packages/persistence/src",
    "apps/api",
):
    p = ROOT / rel
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--symbol",
        action="append",
        default=[],
        help="Symbol to clear (repeatable). Default: full watchlist.",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required to delete keys (dry-run prints targets otherwise).",
    )
    args = parser.parse_args()

    from options_portal_domain.config import load_config, watchlist_symbols
    from options_portal_persistence.kv_store import KvStore

    cfg = load_config()
    symbols = [s.upper() for s in args.symbol] or [
        str(s).upper() for s in watchlist_symbols(cfg)
    ]
    kv = KvStore()
    if not kv.available:
        print("KvStore unavailable — nothing to clear")
        return 1
    print(f"targets={len(symbols)} confirm={args.confirm}")
    cleared: list[str] = []
    for sym in symbols:
        key = f"pine.signal.{sym}"
        if not args.confirm:
            payload = kv.get(key)
            if isinstance(payload, dict) and payload.get("action"):
                print(
                    f"would clear {key} action={payload.get('action')} "
                    f"trend={payload.get('trend')}"
                )
            continue
        if kv.delete(key):
            cleared.append(sym)
            print(f"cleared {key}")
    if not args.confirm:
        print("Re-run with --confirm to delete.")
        return 0
    print(f"cleared {len(cleared)} keys")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
