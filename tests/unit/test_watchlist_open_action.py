"""Watchlist Action sticky while a dry-run position is open (display / mode only)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
API_APP = ROOT / "apps" / "api"
if str(API_APP) not in sys.path:
    sys.path.insert(0, str(API_APP))

from app.mock_data import (  # noqa: E402
    apply_open_trade_display_actions,
    display_action_for_open_side,
    load_open_trade_display_modes,
    rows_for_top_strip,
)
from options_portal_domain.equity import plan_equity_intents  # noqa: E402
from options_portal_domain.types import SignalAction  # noqa: E402
from options_portal_hybrid.explain import pick_top_hybrid_sides  # noqa: E402


def test_display_action_for_open_side_maps_long_short():
    assert display_action_for_open_side("long") == "BUY"
    assert display_action_for_open_side("LONG") == "BUY"
    assert display_action_for_open_side("short") == "SELL"
    assert display_action_for_open_side("SHORT") == "SELL"
    assert display_action_for_open_side(None) is None
    assert display_action_for_open_side("flat") is None


def test_open_long_shows_buy_while_pine_hold():
    """Open LONG → Action BUY even when expire-stale Pine is HOLD."""
    rows = [
        {
            "id": "pine-rel",
            "symbol": "RELIANCE",
            "action": "HOLD",
            "trend": "BEARISH",
            "hybrid_score": 0.0,
            "confidence": 0.3,
        }
    ]
    apply_open_trade_display_actions(rows, {"RELIANCE": "BUY"})
    row = rows[0]
    assert row["action"] == "BUY"
    assert row["display_action"] == "BUY"
    assert row["pine_action"] == "HOLD"
    assert row["open_trade_mode"] == "BUY"
    assert row["action_from_open_trade"] is True
    # Sticky Action must not hide / rewrite Trend
    assert row["trend"] == "BEARISH"


def test_open_short_shows_sell_while_pine_hold():
    rows = [
        {
            "id": "pine-tcs",
            "symbol": "TCS",
            "action": "HOLD",
            "hybrid_score": 0.0,
            "confidence": 0.3,
        }
    ]
    apply_open_trade_display_actions(rows, {"TCS": "SELL"})
    row = rows[0]
    assert row["action"] == "SELL"
    assert row["pine_action"] == "HOLD"
    assert row["action_from_open_trade"] is True


def test_after_close_action_returns_to_pine_or_hold():
    """No open → Action follows pine (HOLD or live BUY/SELL)."""
    rows = [
        {"symbol": "RELIANCE", "action": "HOLD"},
        {"symbol": "INFY", "action": "BUY", "pine_action": "BUY"},
    ]
    apply_open_trade_display_actions(rows, {})  # closed / empty book
    assert rows[0]["action"] == "HOLD"
    assert rows[0]["pine_action"] == "HOLD"
    assert rows[0]["action_from_open_trade"] is False
    assert rows[1]["action"] == "BUY"
    assert rows[1]["pine_action"] == "BUY"
    assert rows[1]["action_from_open_trade"] is False


def test_sticky_action_is_not_an_entry_signal():
    """Sticky BUY while open LONG must not plan a duplicate entry.

    Worker entries use live Pine ``decision.action`` (and skip on HOLD). Same-side
    open already blocks ``plan_equity_intents``. Display Action alone never enters.
    """
    # Display overlay: open LONG, pine expired → HOLD
    rows = [{"symbol": "RELIANCE", "action": "HOLD"}]
    apply_open_trade_display_actions(rows, {"RELIANCE": "BUY"})
    assert rows[0]["action"] == "BUY"
    assert rows[0]["pine_action"] == "HOLD"

    # Entry path uses pine_action / live Pine HOLD — no new intents from HOLD.
    # (Pipeline: use_pine and HOLD → intents=[]; do not call planner with HOLD.)
    pine_for_entry = rows[0]["pine_action"]
    assert pine_for_entry == "HOLD"

    # Same-side open also refuses duplicate even if a stale BUY were passed.
    intents = plan_equity_intents(
        "RELIANCE",
        SignalAction.BUY,
        open_side="long",
        open_qty=1,
        qty=1,
        price=2500.0,
        reason="sticky_display_must_not_reenter",
    )
    assert intents == []

    # Opposite fresh Pine still closes (close-only) — not a display concern.
    flip = plan_equity_intents(
        "RELIANCE",
        SignalAction.EXIT,
        open_side="long",
        open_qty=1,
        qty=1,
        price=2500.0,
        reason="opposite_pine_sell",
    )
    assert len(flip) == 1
    assert flip[0].action_kind == "exit"
    assert flip[0].side == "SELL"


def test_top_strip_uses_pine_action_not_sticky():
    """Open LONG with pine HOLD must not occupy TOP BUY strip."""
    rows = [
        {
            "symbol": "RELIANCE",
            "action": "BUY",  # sticky display
            "pine_action": "HOLD",
            "hybrid_score": 0.0,
            "confidence": 0.3,
            "reason_kind": "pine",
        },
        {
            "symbol": "TCS",
            "action": "BUY",
            "pine_action": "BUY",
            "hybrid_score": 1.0,
            "confidence": 0.9,
            "reason_kind": "pine",
        },
    ]
    strip_rows = rows_for_top_strip(rows)
    sides = pick_top_hybrid_sides(strip_rows, limit=5)
    ce_syms = [r["symbol"] for r in sides["ce"]]
    assert "TCS" in ce_syms
    assert "RELIANCE" not in ce_syms


def test_load_open_trade_display_modes_from_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("PERSISTENCE_PROFILE", "local")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'paper.sqlite'}")

    from options_portal_persistence.paper_trades import PaperTradeStore
    from options_portal_domain.types import OrderResult

    store = PaperTradeStore()
    assert store.available

    long_intent = plan_equity_intents(
        "RELIANCE",
        SignalAction.BUY,
        open_side=None,
        open_qty=0,
        qty=1,
        price=2500.0,
        reason="test long",
    )[0]
    short_intent = plan_equity_intents(
        "TCS",
        SignalAction.SELL,
        open_side=None,
        open_qty=0,
        qty=1,
        price=3900.0,
        reason="test short",
    )[0]
    store.record_simulated_fill(
        long_intent,
        OrderResult("long-1", "simulated", "dry_run", "SIM-L1", None),
        mode="dry_run",
    )
    store.record_simulated_fill(
        short_intent,
        OrderResult("short-1", "simulated", "dry_run", "SIM-S1", None),
        mode="dry_run",
    )

    modes = load_open_trade_display_modes(mode="dry_run")
    assert modes["RELIANCE"] == "BUY"
    assert modes["TCS"] == "SELL"

    rows = [
        {"symbol": "RELIANCE", "action": "HOLD"},
        {"symbol": "TCS", "action": "HOLD"},
        {"symbol": "INFY", "action": "HOLD"},
    ]
    apply_open_trade_display_actions(rows, modes)
    assert rows[0]["action"] == "BUY" and rows[0]["pine_action"] == "HOLD"
    assert rows[1]["action"] == "SELL" and rows[1]["pine_action"] == "HOLD"
    assert rows[2]["action"] == "HOLD" and rows[2]["action_from_open_trade"] is False
