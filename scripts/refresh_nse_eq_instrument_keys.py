#!/usr/bin/env python3
"""Refresh ``config/universe/nse_eq_instrument_keys.csv`` from Upstox NSE master.

Downloads ``https://assets.upstox.com/market-quote/instruments/exchange/NSE.csv.gz``
and maps every equity row in ``nse_fno_underlyings.csv`` to ``NSE_EQ|<ISIN>``.

Usage (from repo root):
  python scripts/refresh_nse_eq_instrument_keys.py
  python scripts/refresh_nse_eq_instrument_keys.py --from-file /tmp/NSE.csv.gz
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import sys
import urllib.request
from pathlib import Path

UPSTOX_NSE_CSV_GZ = (
    "https://assets.upstox.com/market-quote/instruments/exchange/NSE.csv.gz"
)
ALIASES = {
    # Legacy / tests still reference Tata Motors pre-rename symbol
    "TATAMOTORS": "TMPV",
    "LTIM": "LTM",
    "GUJGASLTD": "GUJENERGY",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_upstox_equity_map(raw: bytes) -> dict[str, tuple[str, float, str]]:
    out: dict[str, tuple[str, float, str]] = {}
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
        text = io.TextIOWrapper(gz, encoding="utf-8")
        for row in csv.DictReader(text):
            if row.get("exchange") != "NSE_EQ":
                continue
            if row.get("instrument_type") != "EQUITY":
                continue
            sym = str(row.get("tradingsymbol") or "").strip().upper()
            ikey = str(row.get("instrument_key") or "").strip()
            if not sym or not ikey.startswith("NSE_EQ|"):
                continue
            try:
                lp = float(row.get("last_price") or 0)
            except (TypeError, ValueError):
                lp = 0.0
            name = str(row.get("name") or "").strip()
            prev = out.get(sym)
            if prev is None or lp > prev[1]:
                out[sym] = (ikey, lp, name)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from-file",
        type=Path,
        help="Use a local NSE.csv.gz instead of downloading",
    )
    parser.add_argument(
        "--universe",
        type=Path,
        default=None,
        help="Path to nse_fno_underlyings.csv",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output CSV path",
    )
    args = parser.parse_args()
    root = _repo_root()
    universe_path = args.universe or (root / "config" / "universe" / "nse_fno_underlyings.csv")
    out_path = args.out or (root / "config" / "universe" / "nse_eq_instrument_keys.csv")

    if args.from_file:
        raw = args.from_file.read_bytes()
    else:
        print(f"Downloading {UPSTOX_NSE_CSV_GZ} …", file=sys.stderr)
        with urllib.request.urlopen(UPSTOX_NSE_CSV_GZ, timeout=120) as resp:
            raw = resp.read()

    upstox = _load_upstox_equity_map(raw)
    equities: list[str] = []
    seen: set[str] = set()
    # Always merge active volatile watchlist so refresh keeps ASAHIINDIA / LTM / etc.
    extra_paths = [
        universe_path,
        root / "config" / "universe" / "volatile_fno50_underlyings.csv",
    ]
    for path in extra_paths:
        if not path.is_file():
            continue
        with path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                kind = str(row.get("kind") or "").strip().lower()
                if kind and kind != "equity":
                    continue
                sym = str(row.get("symbol") or "").strip().upper()
                if sym and sym not in seen:
                    seen.add(sym)
                    equities.append(sym)

    rows: list[tuple[str, str, str]] = []
    missing: list[str] = []
    for sym in equities:
        hit = upstox.get(sym)
        if hit is None:
            missing.append(sym)
            continue
        rows.append((sym, hit[0], hit[2]))

    for alias, target in ALIASES.items():
        if alias in {r[0] for r in rows}:
            continue
        hit = upstox.get(target)
        if hit is None:
            missing.append(alias)
            continue
        rows.append((alias, hit[0], f"alias→{target}: {hit[2]}"))

    if missing:
        print(f"ERROR: unresolved equities: {', '.join(missing)}", file=sys.stderr)
        return 1

    rows.sort(key=lambda r: r[0])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["symbol", "instrument_key", "name"])
        writer.writerows(rows)

    print(f"Wrote {len(rows)} keys → {out_path}")
    for probe in ("IOC", "360ONE", "BPCL"):
        for sym, ikey, _name in rows:
            if sym == probe:
                print(f"  {sym}={ikey}")
                break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
