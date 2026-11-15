"""CLI: python -m options_portal_backtest run --symbol NIFTY --bars 200"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from options_portal_domain.tz import ensure_tzdata

ensure_tzdata()

from options_portal_backtest.engine import run_backtest  # noqa: E402


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="options-portal-backtest")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="Dry-run historical hybrid signal replay")
    run_p.add_argument("--symbol", "--symbols", dest="symbol", required=True, help="Symbol (first if comma-list)")
    run_p.add_argument("--from", dest="from_date", default=None, help="YYYY-MM-DD")
    run_p.add_argument("--to", dest="to_date", default=None, help="YYYY-MM-DD")
    run_p.add_argument("--bars", type=int, default=200, help="Max bars (default 200)")
    run_p.add_argument("--fixture", type=Path, default=None, help="Optional candles CSV")
    run_p.add_argument("--json", action="store_true", help="Print JSON summary")

    args = parser.parse_args(argv)
    if args.cmd != "run":
        parser.error(f"unknown command {args.cmd}")

    symbol = str(args.symbol).split(",")[0].strip().upper()
    result = run_backtest(
        symbol,
        start=_parse_date(args.from_date),
        end=_parse_date(args.to_date),
        bars=args.bars,
        dry_run=True,
        fixture_path=args.fixture,
    )
    payload = result.to_dict()
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            f"{result.symbol} dry_run · bars={result.bars} · trades={result.trades} · "
            f"win_rate={result.win_rate:.1%} · pnl={result.pnl:+.2f} · "
            f"max_dd={result.max_drawdown:.2f} · source={result.source}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
