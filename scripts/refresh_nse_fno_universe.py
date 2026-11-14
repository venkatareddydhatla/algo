#!/usr/bin/env python3
"""Refresh config/universe/nse_fno_underlyings.csv from NSE fo_mktlots.csv.

Usage (from repo root, Python 3.12+):

    python scripts/refresh_nse_fno_universe.py
    python scripts/refresh_nse_fno_universe.py --url https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv
    python scripts/refresh_nse_fno_universe.py --from-file /path/to/fo_mktlots.csv

NSE may block datacenter / CI egress. If download fails, keep the checked-in
snapshot and re-run from a workstation with browser-grade access, or pass
--from-file after a manual download.
"""

from __future__ import annotations

import argparse
import csv
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_URL = "https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv"
INDEX_SYMBOLS = {
    "NIFTY",
    "BANKNIFTY",
    "FINNIFTY",
    "MIDCPNIFTY",
    "NIFTYNXT50",
    "NIFTYFPI",
}
INDEX_PRIORITY = [
    "NIFTY",
    "BANKNIFTY",
    "FINNIFTY",
    "MIDCPNIFTY",
    "NIFTYNXT50",
    "NIFTYFPI",
]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def download(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; options-portal-universe-refresh/1.0)",
            "Accept": "text/csv,*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 — intentional public fetch
        return resp.read().decode("utf-8", errors="replace")


def parse_fo_mktlots(text: str) -> list[dict[str, str | int]]:
    rows: list[dict[str, str | int]] = []
    seen: set[str] = set()
    for i, ln in enumerate(text.splitlines()):
        if not ln.strip():
            continue
        parts = [p.strip() for p in ln.split(",")]
        if len(parts) < 2:
            continue
        name, sym = parts[0], parts[1]
        if i == 0 or sym.upper() == "SYMBOL" or not sym or "Derivatives" in name:
            continue
        symbol = sym.upper()
        if symbol in seen:
            continue
        lot = None
        for p in parts[2:]:
            if p.isdigit():
                lot = int(p)
                break
        if lot is None:
            continue
        seen.add(symbol)
        kind = "index" if symbol in INDEX_SYMBOLS else "equity"
        rows.append(
            {
                "symbol": symbol,
                "name": name,
                "kind": kind,
                "expiry_rule": "weekly" if kind == "index" else "monthly",
                "lot_size": lot,
            }
        )

    def sort_key(r: dict[str, str | int]) -> tuple[int, int | str]:
        sym = str(r["symbol"])
        try:
            return (0, INDEX_PRIORITY.index(sym))
        except ValueError:
            return (1, sym)

    rows.sort(key=sort_key)
    return rows


def write_csv(path: Path, rows: list[dict[str, str | int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["symbol", "name", "kind", "expiry_rule", "lot_size"],
        )
        writer.writeheader()
        writer.writerows(rows)


def write_source_note(path: Path, *, url: str, count: int, indices: int, equities: int) -> None:
    stamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    path.write_text(
        "\n".join(
            [
                "# NSE F&O underlyings snapshot",
                "",
                "- **File:** `nse_fno_underlyings.csv` (indices + equity F&O symbols, lot sizes)",
                f"- **Source URL:** {url}",
                "- **Alternate dated archive example:** https://nsearchives.nseindia.com/content/fo/fo_mktlots_31072025.csv",
                f"- **Snapshot date (UTC):** {stamp}",
                f"- **Counts at snapshot:** {indices} index + {equities} equity = {count} underlyings",
                "- **Refresh:** `python scripts/refresh_nse_fno_universe.py`",
                "",
                "Lot sizes change with NSE circulars — verify before live sizing. Dry-run portal use does not place orders.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL, help="NSE fo_mktlots.csv URL")
    parser.add_argument("--from-file", type=Path, help="Parse a local fo_mktlots.csv instead of downloading")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output CSV (default: config/universe/nse_fno_underlyings.csv)",
    )
    args = parser.parse_args(argv)

    out = args.out or (repo_root() / "config" / "universe" / "nse_fno_underlyings.csv")
    source_md = out.parent / "SOURCE.md"

    try:
        if args.from_file:
            text = args.from_file.read_text(encoding="utf-8", errors="replace")
            url_note = f"local:{args.from_file}"
        else:
            text = download(args.url)
            url_note = args.url
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        print(
            f"ERROR: could not fetch NSE universe ({exc}).\n"
            "Keep the checked-in snapshot, or download fo_mktlots.csv manually and re-run with --from-file.",
            file=sys.stderr,
        )
        return 1

    rows = parse_fo_mktlots(text)
    if len(rows) < 50:
        print(f"ERROR: parsed only {len(rows)} rows — refusing to overwrite snapshot", file=sys.stderr)
        return 2

    write_csv(out, rows)
    indices = sum(1 for r in rows if r["kind"] == "index")
    equities = sum(1 for r in rows if r["kind"] == "equity")
    write_source_note(source_md, url=url_note, count=len(rows), indices=indices, equities=equities)
    print(f"Wrote {len(rows)} underlyings ({indices} index, {equities} equity) -> {out}")
    print(f"Updated {source_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
