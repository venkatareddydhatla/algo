"""Soft EOD flatten + light index market-sentiment gates."""

from __future__ import annotations

from datetime import datetime

import pytest

from options_portal_domain.equity import (
    plan_equity_intents,
    profit_book_exit_qty,
    unrealized_pnl_pct,
)
from options_portal_domain.session_gates import (
    MarketSentimentPolicy,
    MorningProfitBookPolicy,
    SoftEodPolicy,
    at_or_after_ist,
    compute_index_bias,
    entry_conflicts_with_index,
    filter_entry_intents,
    is_cash_market_open,
    is_trading_session,
    morning_profit_book_decision,
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


def test_cash_market_open_until_close_ist():
    policy = SoftEodPolicy(open_ist="09:15", close_ist="15:30", no_new_entries_after="15:20")
    # Still OPEN for UI after entry cutoff
    assert is_cash_market_open(
        datetime(2026, 11, 16, 15, 25, 0), policy=policy, trading_day=True
    )
    assert not is_cash_market_open(
        datetime(2026, 11, 16, 15, 30, 0), policy=policy, trading_day=True
    )
    assert policy.in_cash_session(datetime(2026, 11, 16, 15, 29, 0))
    assert policy.past_session_close(datetime(2026, 11, 16, 15, 30, 0))


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


def test_morning_profit_book_window_and_dynamic_thresholds():
    """09:15–10:00 IST: ATR-aware TP/stop; after 10:00 → none; pct fallback."""
    policy = MorningProfitBookPolicy(
        enabled=True,
        start_ist="09:15",
        until_ist="10:00",
        tp_min_pct=0.3,
        tp_atr_mult=0.5,
        stop_min_pct=0.5,
        stop_atr_mult=0.75,
        exit_fraction=1.0,
    )
    soft = SoftEodPolicy.from_config(
        {
            "trading": {
                "session": {
                    "morning_profit_book": {
                        "enabled": True,
                        "start_ist": "09:15",
                        "until_ist": "10:00",
                        "tp_min_pct": 0.3,
                        "stop_min_pct": 0.5,
                    }
                }
            }
        }
    )
    assert soft.morning_tp_until_ist == "10:00"
    assert soft.in_morning_profit_window(datetime(2026, 11, 17, 9, 15, 0))
    assert soft.in_morning_profit_window(datetime(2026, 11, 17, 10, 0, 0))
    assert not soft.in_morning_profit_window(datetime(2026, 11, 17, 10, 1, 0))
    assert not soft.in_morning_profit_window(datetime(2026, 11, 17, 10, 15, 0))

    # % only (no ATR): long entry 100 → mark 100.35 = +0.35% → book
    hit = morning_profit_book_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=100.35,
        policy=policy,
        now=datetime(2026, 11, 17, 9, 45, 0),
        atr14=None,
    )
    assert hit["action"] == "profit_book"
    assert hit["exit_qty"] == 1
    assert hit["unrealized_pct"] == pytest.approx(0.35)
    assert "morning_profit_book" in hit["reason"]
    assert hit["thresholds"]["mode"] == "pct_only"

    # Just under TP floor (+0.25% < 0.3%)
    miss = morning_profit_book_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=100.25,
        policy=policy,
        now=datetime(2026, 11, 17, 9, 45, 0),
        atr14=None,
    )
    assert miss["action"] == "none"
    assert miss["unrealized_pct"] == pytest.approx(0.25)

    # Morning stop: −0.5% with no ATR
    stop = morning_profit_book_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=99.50,
        policy=policy,
        now=datetime(2026, 11, 17, 9, 30, 0),
        atr14=None,
    )
    assert stop["action"] == "stop"
    assert "morning_stop" in stop["reason"]

    # ATR raises TP: entry 100, ATR14=2 → 0.5×ATR=1.0 > 0.3%×100=0.3 → need +1.0
    atr_hold = morning_profit_book_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=100.50,  # +0.5% = 0.5 ₹ < 1.0 ATR leg
        policy=policy,
        now=datetime(2026, 11, 17, 9, 40, 0),
        atr14=2.0,
    )
    assert atr_hold["action"] == "none"
    assert atr_hold["thresholds"]["mode"] == "atr"
    atr_hit = morning_profit_book_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=101.00,  # +1.0 ₹ = 0.5×ATR
        policy=policy,
        now=datetime(2026, 11, 17, 9, 40, 0),
        atr14=2.0,
    )
    assert atr_hit["action"] == "profit_book"

    # ATR raises stop: 0.75×ATR=1.5 > 0.5%×100=0.5 → stop at −1.5
    atr_stop_miss = morning_profit_book_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=99.20,  # −0.8 ₹ (worse than % floor but above ATR stop)
        policy=policy,
        now=datetime(2026, 11, 17, 9, 40, 0),
        atr14=2.0,
    )
    assert atr_stop_miss["action"] == "none"
    atr_stop = morning_profit_book_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=98.50,  # −1.5 ₹
        policy=policy,
        now=datetime(2026, 11, 17, 9, 40, 0),
        atr14=2.0,
    )
    assert atr_stop["action"] == "stop"
    assert "morning_stop" in atr_stop["reason"]

    # After 10:00 — no morning TP/stop
    late = morning_profit_book_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=101.0,
        policy=policy,
        now=datetime(2026, 11, 17, 10, 1, 0),
        atr14=2.0,
    )
    assert late["action"] == "none"
    assert late["active"] is False
    assert "outside" in late["reason"]

    # Indices never morning-TP/stop
    idx = morning_profit_book_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=101.0,
        policy=policy,
        now=datetime(2026, 11, 17, 9, 30, 0),
        is_index=True,
        atr14=2.0,
    )
    assert idx["action"] == "none"
    assert "index_not_equity" in idx["reason"]

    # Short geometry (% only): +0.5% profit
    short_hit = morning_profit_book_decision(
        open_side="short",
        open_qty=1,
        entry_avg=200.0,
        mark=199.0,
        policy=policy,
        now=datetime(2026, 11, 17, 9, 20, 0),
        atr14=None,
    )
    assert short_hit["action"] == "profit_book"
    assert unrealized_pnl_pct("short", 200.0, 199.0) == pytest.approx(0.5)


def test_profit_book_exit_qty_full_and_scale_out():
    """qty=1 → full exit; fraction=0.5 with qty>1 ready for later scale-out."""
    assert profit_book_exit_qty(1, fraction=1.0) == 1
    assert profit_book_exit_qty(4, fraction=0.5) == 2
    assert profit_book_exit_qty(1, fraction=0.5) == 1  # at least 1 when open
    assert profit_book_exit_qty(0, fraction=1.0) == 0

    half = MorningProfitBookPolicy(exit_fraction=0.5, tp_min_pct=0.3)
    decision = morning_profit_book_decision(
        open_side="long",
        open_qty=4,
        entry_avg=100.0,
        mark=100.5,
        policy=half,
        now=datetime(2026, 11, 17, 9, 30, 0),
        atr14=None,
    )
    assert decision["action"] == "profit_book"
    assert decision["exit_qty"] == 2

    intents = plan_equity_intents(
        "RELIANCE",
        SignalAction.EXIT,
        open_side="long",
        open_qty=4,
        qty=1,
        price=100.5,
        reason=decision["reason"],
        exit_qty=decision["exit_qty"],
    )
    assert len(intents) == 1
    assert intents[0].qty == 2
    assert intents[0].action_kind == "exit"
