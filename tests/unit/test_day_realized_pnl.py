"""Day realized = sum of closed round-trip PnL for NSE session IST day."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from options_portal_domain.calendar import pnl_ist_day, previous_nse_trading_day
from options_portal_domain.equity import plan_equity_intents
from options_portal_domain.round_trips import sum_closed_realized_pnl
from options_portal_domain.types import OrderResult, SignalAction
from options_portal_domain.tz import ist
from options_portal_persistence import paper_trades as pt
from options_portal_persistence.paper_trades import PaperTradeStore


@pytest.fixture()
def paper_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "paper.db"
    monkeypatch.setenv("PERSISTENCE_PROFILE", "local")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    return db


def test_pnl_ist_day_rolls_at_session_open_not_midnight():
    # Mon 16 Nov 2026 after soft-flatten — same calendar/session day
    after_close = datetime(2026, 11, 16, 16, 30, tzinfo=ist())
    assert pnl_ist_day(after_close) == date(2026, 11, 16)

    # Just after IST midnight Tue — still attribute to Mon session
    after_midnight = datetime(2026, 11, 17, 0, 30, tzinfo=ist())
    assert pnl_ist_day(after_midnight) == date(2026, 11, 16)

    # Tue after cash open — new session day
    tue_open = datetime(2026, 11, 17, 9, 15, tzinfo=ist())
    assert pnl_ist_day(tue_open) == date(2026, 11, 17)

    # Weekend → prior Friday
    sat = datetime(2026, 11, 21, 12, 0, tzinfo=ist())
    assert pnl_ist_day(sat) == date(2026, 11, 20)


def test_previous_nse_trading_day_skips_weekend():
    assert previous_nse_trading_day(date(2026, 11, 16)) == date(2026, 11, 13)


def test_sum_closed_realized_pnl_skips_none():
    assert sum_closed_realized_pnl([{"pnl": 10.0}, {"pnl": None}, {"pnl": -2.5}]) == pytest.approx(
        7.5
    )


def test_day_realized_matches_closed_round_trips_after_ist_midnight(
    paper_env: Path,
):
    """Venkata symptom: Closed shows 16 Nov PnL; Day realized must not be ₹0 overnight."""
    store = PaperTradeStore()
    entry_ts = datetime(2026, 11, 16, 5, 0, tzinfo=timezone.utc)  # 10:30 IST
    exit_ts = datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc)  # 15:30 IST
    open_ts = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)  # after-hours open
    # PR #32 / purge time ≈ 18:58 UTC = 00:28 IST Tue
    overnight = datetime(2026, 11, 16, 18, 58, tzinfo=timezone.utc)

    with patch.object(pt, "_utc_now", return_value=entry_ts):
        entry = plan_equity_intents(
            "BPCL",
            SignalAction.BUY_CE_BIAS,
            open_side=None,
            open_qty=0,
            qty=1,
            price=500.0,
            reason="entry",
        )[0]
        store.record_simulated_fill(
            entry, OrderResult("e1", "simulated", "dry_run", "SIM-e1", None)
        )
    with patch.object(pt, "_utc_now", return_value=exit_ts):
        exit_i = plan_equity_intents(
            "BPCL",
            SignalAction.HOLD,
            open_side="long",
            open_qty=1,
            qty=1,
            price=510.0,
            reason="soft_flatten",
        )[0]
        store.record_simulated_fill(
            exit_i, OrderResult("x1", "simulated", "dry_run", "SIM-x1", None)
        )
    with patch.object(pt, "_utc_now", return_value=open_ts):
        bog = plan_equity_intents(
            "MUTHOOTFIN",
            SignalAction.BUY_CE_BIAS,
            open_side=None,
            open_qty=0,
            qty=1,
            price=3200.0,
            reason="after-hours",
        )[0]
        store.record_simulated_fill(
            bog, OrderResult("o1", "simulated", "dry_run", "SIM-o1", None)
        )

    with patch.object(pt, "_utc_now", return_value=overnight):
        store.purge_open_positions(confirm=True, archive=True)
        snap = store.summary()
        assert snap["positions"] == []
        assert snap["day_unrealized"] == pytest.approx(0.0)
        assert snap["day_ist"] == "2026-11-16"
        assert snap["closed_trades_total"] == 1
        assert snap["closed_trades"][0]["pnl"] == pytest.approx(10.0)
        assert snap["day_realized"] == pytest.approx(10.0)
        assert store.day_realized_pnl() == pytest.approx(10.0)
        # Explicit selected day still works
        assert store.day_realized_pnl(day_ist="2026-11-16") == pytest.approx(10.0)
        assert store.day_realized_pnl(day_ist="2026-11-17") == pytest.approx(0.0)
