"""Shared paper-ledger round-trip pairing (domain)."""

from __future__ import annotations

from options_portal_domain.round_trips import (
    closed_round_trip_fill_ids,
    closed_trades_for_ist_day,
    open_book_fill_ids,
    pair_round_trip_trades,
    sort_closed_trades_newest_first,
)


def test_pair_round_trip_long_and_omit_open():
    fills = [
        {
            "id": "1",
            "symbol": "TCS",
            "side": "BUY",
            "qty": 1,
            "price": 3900,
            "action_kind": "entry",
            "position_side": "long",
            "ts_ist": "2026-11-15T11:00:00+05:30",
            "ts_ist_display": "2026-11-15 11:00 IST",
            "reason": "entry hybrid",
            "realized_pnl": None,
            "dry_run": True,
        },
        {
            "id": "2",
            "symbol": "TCS",
            "side": "SELL",
            "qty": 1,
            "price": 3912.5,
            "action_kind": "exit",
            "position_side": None,
            "ts_ist": "2026-11-15T14:00:00+05:30",
            "ts_ist_display": "2026-11-15 14:00 IST",
            "reason": "signal_exit · close long",
            "realized_pnl": 12.5,
            "dry_run": True,
        },
        {
            "id": "3",
            "symbol": "INFY",
            "side": "SELL",
            "qty": 2,
            "price": 1500,
            "action_kind": "entry",
            "position_side": "short",
            "ts_ist": "2026-11-15T12:00:00+05:30",
            "ts_ist_display": "2026-11-15 12:00 IST",
            "reason": "short entry",
            "realized_pnl": None,
            "dry_run": True,
        },
    ]
    trades = pair_round_trip_trades(fills)
    assert len(trades) == 1
    assert trades[0]["symbol"] == "TCS"
    assert trades[0]["side"] == "LONG"
    assert trades[0]["entry_price"] == 3900
    assert trades[0]["exit_price"] == 3912.5
    assert trades[0]["pnl"] == 12.5
    assert trades[0]["dry_run"] is True
    assert closed_trades_for_ist_day(trades, "2026-11-15") == trades
    # Closed purge targets TCS entry+exit only; open INFY entry stays
    assert closed_round_trip_fill_ids(fills) == {"1", "2"}
    # Open-book purge targets unpaired INFY entry only
    assert open_book_fill_ids(fills) == {"3"}


def test_sort_closed_trades_newest_first():
    trades = [
        {
            "symbol": "A",
            "exit_ts_ist_display": "2026-11-14 10:00 IST",
            "exit_fill_id": "1",
        },
        {
            "symbol": "B",
            "exit_ts_ist_display": "2026-11-15 12:00 IST",
            "exit_fill_id": "2",
        },
    ]
    ordered = sort_closed_trades_newest_first(trades)
    assert [t["symbol"] for t in ordered] == ["B", "A"]
