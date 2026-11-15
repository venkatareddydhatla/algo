"""Pair paper-ledger fills into closed round-trips (Backtest Fills layout).

Shared by Gmail daily summary and the Trades & P&L API/UI so pairing stays
consistent. Still-open legs are omitted — use Open positions, do not invent exits.
"""

from __future__ import annotations

from typing import Any

_ENTRY_ACTION_KINDS = frozenset({"entry", "flip_open"})
_EXIT_ACTION_KINDS = frozenset({"exit", "flip_close"})


def fill_sort_key(fill: dict[str, Any]) -> tuple[str, str]:
    return (
        str(fill.get("ts_ist") or fill.get("ts") or fill.get("ts_ist_display") or ""),
        str(fill.get("id") or ""),
    )


def is_entry_fill(fill: dict[str, Any]) -> bool:
    """True for opens (entry / flip_open); False for closes.

    Falls back to realized_pnl when ``action_kind`` is missing (legacy rows).
    """
    kind = str(fill.get("action_kind") or "").strip().lower()
    if kind in _ENTRY_ACTION_KINDS:
        return True
    if kind in _EXIT_ACTION_KINDS:
        return False
    return fill.get("realized_pnl") is None


def held_side_label(entry: dict[str, Any] | None, exit_fill: dict[str, Any]) -> str:
    """Map equity position to LONG/SHORT (Backtest CE≈long, PE≈short)."""
    if entry is not None:
        ps = str(entry.get("position_side") or "").strip().lower()
        if ps in {"long", "short"}:
            return ps.upper()
        order = str(entry.get("side") or "").strip().upper()
        if order == "BUY":
            return "LONG"
        if order == "SELL":
            return "SHORT"
    # Infer from closing order: SELL closes long, BUY covers short
    exit_side = str(exit_fill.get("side") or "").strip().upper()
    if exit_side == "SELL":
        return "LONG"
    if exit_side == "BUY":
        return "SHORT"
    ps = str(exit_fill.get("position_side") or "").strip().lower()
    if ps in {"long", "short"}:
        return ps.upper()
    return "—"


def pair_round_trip_trades(fills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pair paper ledger fills into closed round-trips (Backtest Fills layout).

    Chronological per-symbol: ``entry`` / ``flip_open`` opens a leg;
    ``exit`` / ``flip_close`` closes it into one row. Still-open legs are omitted
    (use the Open positions section — do not invent exits).
    """
    ordered = sorted(fills, key=fill_sort_key)
    open_by_symbol: dict[str, dict[str, Any]] = {}
    closed: list[dict[str, Any]] = []
    for fill in ordered:
        symbol = str(fill.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        if is_entry_fill(fill):
            open_by_symbol[symbol] = fill
            continue
        entry = open_by_symbol.pop(symbol, None)
        closed.append(
            {
                "symbol": symbol,
                "side": held_side_label(entry, fill),
                "entry_ts_ist_display": (
                    str(entry.get("ts_ist_display") or "—") if entry else "—"
                ),
                "exit_ts_ist_display": str(fill.get("ts_ist_display") or "—"),
                "entry_price": entry.get("price") if entry else None,
                "exit_price": fill.get("price"),
                "pnl": fill.get("realized_pnl"),
                "reason": str(fill.get("reason") or "").strip(),
                "qty": fill.get("qty")
                if fill.get("qty") is not None
                else (entry.get("qty") if entry else None),
                "dry_run": bool(
                    fill.get("dry_run")
                    if fill.get("dry_run") is not None
                    else (entry.get("dry_run") if entry and entry.get("dry_run") is not None else True)
                ),
                "exit_fill_id": fill.get("id"),
                "entry_fill_id": entry.get("id") if entry else None,
            }
        )
    return closed


def closed_trades_for_ist_day(
    trades: list[dict[str, Any]],
    day_ist: str,
) -> list[dict[str, Any]]:
    """Keep round-trips whose exit timestamp falls on ``YYYY-MM-DD`` (IST)."""
    prefix = (day_ist or "").strip()
    if not prefix:
        return list(trades)
    return [
        t
        for t in trades
        if str(t.get("exit_ts_ist_display") or "").startswith(prefix)
    ]


def sort_closed_trades_newest_first(
    trades: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Newest exit first (Trades UI / pagination)."""
    return sorted(
        trades,
        key=lambda t: (
            str(t.get("exit_ts_ist_display") or ""),
            str(t.get("symbol") or ""),
            str(t.get("exit_fill_id") or ""),
        ),
        reverse=True,
    )
