"""Soft EOD flatten + light index market-sentiment gates."""

from __future__ import annotations

from datetime import datetime

import pytest

from options_portal_domain.equity import plan_equity_intents
from options_portal_domain.session_gates import (
    MarketSentimentPolicy,
    SoftEodPolicy,
    at_or_after_ist,
    compute_index_bias,
    entry_conflicts_with_index,
    filter_entry_intents,
    is_trading_session,
    overnight_hold_allowed,
    soft_flatten_decision,
)
from options_portal_domain.types import SignalAction


def test_at_or_after_ist_boundary():
    noon = datetime(2026, 11, 15, 12, 0, 0)
    assert at_or_after_ist("15:20", noon) is False
    assert at_or_after_ist("15:20", datetime(2026, 11, 15, 15, 20, 0)) is True
    assert at_or_after_ist("15:20", datetime(2026, 11, 15, 15, 40, 0)) is True


def test_allow_new_entries_blocks_night_and_preopen():
    """Regression: past_no_new_entries alone allowed 00:01 IST entries."""
    policy = SoftEodPolicy(open_ist="09:15", no_new_entries_after="15:20")
    # Monday 2026-11-16 — trading day; midnight must NOT allow entries
    midnight = datetime(2026, 11, 17, 0, 1, 0)  # Tue 00:01 IST (MUTHOOTFIN bug)
    assert policy.past_no_new_entries(midnight) is False  # old bug path
    assert policy.before_session_open(midnight) is True
    assert policy.allow_new_entries(midnight) is False
    assert is_trading_session(midnight, policy=policy, trading_day=True) is False

    preopen = datetime(2026, 11, 16, 9, 14, 0)
    assert policy.allow_new_entries(preopen) is False
    assert is_trading_session(preopen, policy=policy, trading_day=True) is False

    open_exact = datetime(2026, 11, 16, 9, 15, 0)
    assert policy.allow_new_entries(open_exact) is True
    assert is_trading_session(open_exact, policy=policy, trading_day=True) is True

    mid = datetime(2026, 11, 16, 12, 0, 0)
    assert policy.allow_new_entries(mid) is True

    cutoff = datetime(2026, 11, 16, 15, 20, 0)
    assert policy.allow_new_entries(cutoff) is False
    assert is_trading_session(cutoff, policy=policy, trading_day=True) is False

    # Weekend / holiday: clock in window still blocked when trading_day=False
    assert is_trading_session(mid, policy=policy, trading_day=False) is False


def test_soft_eod_policy_from_config():
    policy = SoftEodPolicy.from_config(
        {
            "trading": {
                "session": {
                    "open": "09:15",
                    "close": "15:30",
                    "no_new_entries_after": "15:20",
                    "soft_flatten_ist": "15:20",
                    "soft_flatten_until_ist": "15:40",
                    "overnight_hold_min_hybrid_abs": 0.70,
                }
            }
        }
    )
    assert policy.close_ist == "15:30"
    assert policy.soft_flatten_ist == "15:20"
    assert policy.soft_flatten_until_ist == "15:40"
    assert policy.overnight_hold_min_hybrid_abs == 0.70
    assert "15:40" in policy.user_hours_note
    assert policy.past_soft_flatten(datetime(2026, 11, 15, 15, 19, 0)) is False
    assert policy.past_soft_flatten(datetime(2026, 11, 15, 15, 20, 0)) is True
    assert policy.in_soft_flatten_window(datetime(2026, 11, 15, 15, 20, 0)) is True
    assert policy.in_soft_flatten_window(datetime(2026, 11, 15, 15, 40, 0)) is True
    assert policy.in_soft_flatten_window(datetime(2026, 11, 15, 15, 41, 0)) is False
    assert policy.past_soft_flatten_until(datetime(2026, 11, 15, 17, 13, 0)) is True


def test_overnight_hold_same_side_conviction():
    assert overnight_hold_allowed("long", 0.75, min_abs=0.70) is True
    assert overnight_hold_allowed("long", 0.50, min_abs=0.70) is False
    assert overnight_hold_allowed("long", -0.90, min_abs=0.70) is False
    assert overnight_hold_allowed("short", -0.80, min_abs=0.70) is True
    assert overnight_hold_allowed("short", 0.80, min_abs=0.70) is False
    assert overnight_hold_allowed(None, 0.99, min_abs=0.70) is False


def test_soft_flatten_closes_without_conviction():
    policy = SoftEodPolicy(soft_flatten_ist="15:20", overnight_hold_min_hybrid_abs=0.70)
    now = datetime(2026, 11, 15, 15, 25, 0)
    decision = soft_flatten_decision(
        open_side="long",
        open_qty=1,
        hybrid_score=0.40,
        policy=policy,
        now=now,
    )
    assert decision["action"] == "flatten"
    assert "soft_flatten" in decision["reason"]


def test_soft_flatten_allows_overnight_on_strong_same_side():
    policy = SoftEodPolicy(soft_flatten_ist="15:20", overnight_hold_min_hybrid_abs=0.70)
    now = datetime(2026, 11, 15, 15, 25, 0)
    decision = soft_flatten_decision(
        open_side="short",
        open_qty=1,
        hybrid_score=-0.85,
        policy=policy,
        now=now,
    )
    assert decision["action"] == "overnight_hold"
    assert "conviction" in decision["reason"]


def test_soft_flatten_window_end_still_conviction_based():
    """At soft_flatten_until (15:40) still conviction-based — not a hard force close."""
    policy = SoftEodPolicy(
        soft_flatten_ist="15:20",
        soft_flatten_until_ist="15:40",
        overnight_hold_min_hybrid_abs=0.70,
    )
    late = datetime(2026, 11, 15, 15, 40, 0)
    hold = soft_flatten_decision(
        open_side="long",
        open_qty=1,
        hybrid_score=0.90,
        policy=policy,
        now=late,
    )
    flatten = soft_flatten_decision(
        open_side="long",
        open_qty=1,
        hybrid_score=0.10,
        policy=policy,
        now=late,
    )
    assert hold["action"] == "overnight_hold"
    assert flatten["action"] == "flatten"


def test_soft_flatten_refused_after_until_cutoff():
    """After soft_flatten_until_ist: refuse new soft_flatten (late worker / hung scan)."""
    policy = SoftEodPolicy(
        soft_flatten_ist="15:20",
        soft_flatten_until_ist="15:40",
        overnight_hold_min_hybrid_abs=0.70,
    )
    after = datetime(2026, 11, 15, 17, 13, 0)
    decision = soft_flatten_decision(
        open_side="long",
        open_qty=1,
        hybrid_score=0.65,
        policy=policy,
        now=after,
    )
    assert decision["action"] == "none"
    assert decision["active"] is False
    assert "refused" in decision["reason"]
    assert "15:40" in decision["reason"]


def test_filter_entry_intents_keeps_exits():
    entry = plan_equity_intents(
        "RELIANCE",
        SignalAction.BUY_CE_BIAS,
        open_side=None,
        open_qty=0,
        qty=1,
        price=100.0,
    )
    exit_i = plan_equity_intents(
        "RELIANCE",
        SignalAction.HOLD,
        open_side="long",
        open_qty=1,
        qty=1,
        price=100.0,
    )
    kept = filter_entry_intents(entry + exit_i, allow_entries=False)
    assert len(kept) == 1
    assert kept[0].action_kind == "exit"


def test_compute_index_bias_average_and_noisy():
    ok = compute_index_bias({"NIFTY": 0.60, "BANKNIFTY": 0.50})
    assert ok["bias"] == pytest.approx(0.55)
    assert ok["label"] == "bullish"

    noisy = compute_index_bias(
        {"NIFTY": 0.60, "BANKNIFTY": -0.55},
        disagree_min_abs=0.25,
    )
    assert noisy["bias"] is None
    assert noisy["label"] == "noisy"

    missing = compute_index_bias({})
    assert missing["label"] == "unavailable"


def test_entry_conflicts_with_strong_index():
    blocked, reason = entry_conflicts_with_index(
        SignalAction.BUY_PE_BIAS, 0.70, conflict_abs=0.55
    )
    assert blocked is True
    assert "blocks PE" in reason

    blocked2, _ = entry_conflicts_with_index(
        SignalAction.BUY_CE_BIAS, -0.70, conflict_abs=0.55
    )
    assert blocked2 is True

    # Same-side / weak — do not over-block
    ok, _ = entry_conflicts_with_index(SignalAction.BUY_CE_BIAS, 0.70, conflict_abs=0.55)
    assert ok is False
    weak, _ = entry_conflicts_with_index(SignalAction.BUY_PE_BIAS, 0.30, conflict_abs=0.55)
    assert weak is False
    skip, _ = entry_conflicts_with_index(SignalAction.BUY_PE_BIAS, None, conflict_abs=0.55)
    assert skip is False


def test_market_sentiment_policy_from_config():
    policy = MarketSentimentPolicy.from_config(
        {
            "trading": {
                "market_sentiment": {
                    "enabled": True,
                    "symbols": ["NIFTY", "BANKNIFTY"],
                    "conflict_abs": 0.55,
                }
            }
        }
    )
    assert policy.enabled is True
    assert policy.symbols == ("NIFTY", "BANKNIFTY")
