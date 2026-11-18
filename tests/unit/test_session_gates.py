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
    AtrTrailScaleOutPolicy,
    MarketSentimentPolicy,
    MorningProfitBookPolicy,
    SoftEodPolicy,
    allow_open_book_risk_exits,
    at_or_after_ist,
    atr_trail_scale_out_decision,
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
    policy = SoftEodPolicy(open_ist="09:15", no_new_entries_after="11:30")
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

    # Dual-session: rest-of-day entries still allowed before 11:30
    mid_morning = datetime(2026, 11, 16, 10, 30, 0)
    assert policy.allow_new_entries(mid_morning) is True
    assert is_trading_session(mid_morning, policy=policy, trading_day=True) is True

    before_cutoff = datetime(2026, 11, 16, 11, 29, 0)
    assert policy.allow_new_entries(before_cutoff) is True

    # No new entries after 11:30 IST (risk exits still run)
    cutoff = datetime(2026, 11, 16, 11, 30, 0)
    assert policy.allow_new_entries(cutoff) is False
    assert is_trading_session(cutoff, policy=policy, trading_day=True) is False

    midday = datetime(2026, 11, 16, 12, 0, 0)
    assert policy.allow_new_entries(midday) is False
    assert is_trading_session(midday, policy=policy, trading_day=True) is False

    # Weekend / holiday: clock in window still blocked when trading_day=False
    assert is_trading_session(before_cutoff, policy=policy, trading_day=False) is False


def test_dual_session_entry_cutoff_1130_keeps_risk_exits():
    """Entries stop at 11:30; ATR/morning risk exits and soft flatten still allowed."""
    policy = SoftEodPolicy.from_config(
        {
            "trading": {
                "session": {
                    "open": "09:15",
                    "close": "15:30",
                    "no_new_entries_after": "11:30",
                    "soft_flatten_ist": "15:20",
                    "soft_flatten_until_ist": "15:40",
                }
            }
        }
    )
    assert policy.no_new_entries_after == "11:30"
    assert policy.soft_flatten_ist == "15:20"

    after_entries = datetime(2026, 11, 18, 12, 0, 0)
    assert is_trading_session(after_entries, policy=policy, trading_day=True) is False
    assert allow_open_book_risk_exits(after_entries, policy=policy, trading_day=True) is True
    assert policy.in_risk_exit_window(after_entries) is True

    soft = datetime(2026, 11, 18, 15, 25, 0)
    assert is_trading_session(soft, policy=policy, trading_day=True) is False
    assert policy.in_soft_flatten_window(soft) is True


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
    """09:15–10:00 IST: ATR-aware TP/stop (1.0%/1.5×ATR); after 10:00 → none."""
    policy = MorningProfitBookPolicy(
        enabled=True,
        start_ist="09:15",
        until_ist="10:00",
        tp_min_pct=1.0,
        tp_atr_mult=1.5,
        stop_min_pct=1.0,
        stop_atr_mult=1.25,
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
                        "tp_min_pct": 1.0,
                        "tp_atr_mult": 1.5,
                        "stop_min_pct": 1.0,
                        "stop_atr_mult": 1.25,
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

    # Dual-session defaults from config / class
    dual = MorningProfitBookPolicy.from_config({"trading": {"session": {}}})
    assert dual.tp_min_pct == 1.0
    assert dual.tp_atr_mult == 1.5
    assert dual.stop_min_pct == 1.0
    assert dual.stop_atr_mult == 1.25

    # Tiny +0.3–0.6% scalp must NOT fire (Day3 churn diagnosis)
    for mark, pct in ((100.35, 0.35), (100.60, 0.60)):
        scalp = morning_profit_book_decision(
            open_side="long",
            open_qty=1,
            entry_avg=100.0,
            mark=mark,
            policy=policy,
            now=datetime(2026, 11, 17, 9, 45, 0),
            atr14=None,
        )
        assert scalp["action"] == "none"
        assert scalp["unrealized_pct"] == pytest.approx(pct)

    # % only (no ATR): long entry 100 → mark 101.00 = +1.0% ≥ 1.0% → book
    hit = morning_profit_book_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=101.00,
        policy=policy,
        now=datetime(2026, 11, 17, 9, 45, 0),
        atr14=None,
    )
    assert hit["action"] == "profit_book"
    assert hit["exit_qty"] == 1
    assert hit["unrealized_pct"] == pytest.approx(1.0)
    assert "morning_profit_book" in hit["reason"]
    assert hit["thresholds"]["mode"] == "pct_only"

    # Just under TP floor (+0.90% < 1.0%)
    miss = morning_profit_book_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=100.90,
        policy=policy,
        now=datetime(2026, 11, 17, 9, 45, 0),
        atr14=None,
    )
    assert miss["action"] == "none"
    assert miss["unrealized_pct"] == pytest.approx(0.90)

    # Morning stop: −1.0% with no ATR
    stop = morning_profit_book_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=99.00,
        policy=policy,
        now=datetime(2026, 11, 17, 9, 30, 0),
        atr14=None,
    )
    assert stop["action"] == "stop"
    assert "morning_stop" in stop["reason"]

    # ATR raises TP: entry 100, ATR14=2 → 1.5×ATR=3.0 > 1.0%×100=1.0 → need +3.0
    atr_hold = morning_profit_book_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=102.00,  # +2.0% = 2.0 ₹ < 3.0 ATR leg
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
        mark=103.00,  # +3.0 ₹ = 1.5×ATR
        policy=policy,
        now=datetime(2026, 11, 17, 9, 40, 0),
        atr14=2.0,
    )
    assert atr_hit["action"] == "profit_book"

    # ATR raises stop: 1.25×ATR=2.5 > 1.0%×100=1.0 → stop at −2.5
    atr_stop_miss = morning_profit_book_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=98.00,  # −2.0 ₹ (worse than % floor but above ATR stop)
        policy=policy,
        now=datetime(2026, 11, 17, 9, 40, 0),
        atr14=2.0,
    )
    assert atr_stop_miss["action"] == "none"
    atr_stop = morning_profit_book_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=97.50,  # −2.5 ₹
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
        mark=103.0,
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
        mark=103.0,
        policy=policy,
        now=datetime(2026, 11, 17, 9, 30, 0),
        is_index=True,
        atr14=2.0,
    )
    assert idx["action"] == "none"
    assert "index_not_equity" in idx["reason"]

    # Short geometry (% only): +1.0% profit (≥ 1.0% floor)
    short_hit = morning_profit_book_decision(
        open_side="short",
        open_qty=1,
        entry_avg=200.0,
        mark=198.0,
        policy=policy,
        now=datetime(2026, 11, 17, 9, 20, 0),
        atr14=None,
    )
    assert short_hit["action"] == "profit_book"
    assert unrealized_pnl_pct("short", 200.0, 198.0) == pytest.approx(1.0)


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


def test_atr_trail_policy_from_config_under_trading_risk():
    policy = AtrTrailScaleOutPolicy.from_config(
        {
            "trading": {
                "risk": {
                    "atr_trail_scale_out": {
                        "enabled": True,
                        "stop_atr_mult": 2.0,
                        "stop_pct": 1.2,
                        "trail_activate_atr_mult": 1.5,
                        "trail_activate_pct": 0.8,
                        "trail_atr_mult": 1.2,
                        "profit_atr_mult": 2.5,
                        "profit_pct": 1.2,
                        "scale_out_fraction": 0.5,
                    }
                }
            }
        }
    )
    assert policy.enabled is True
    assert policy.stop_atr_mult == 2.0
    assert policy.trail_activate_atr_mult == 1.5
    assert policy.trail_atr_mult == 1.2
    assert policy.profit_atr_mult == 2.5
    assert policy.scale_out_fraction == 0.5

    # Dual-session class defaults when YAML omits block
    defaults = AtrTrailScaleOutPolicy()
    assert defaults.stop_atr_mult == 2.0
    assert defaults.profit_atr_mult == 2.5
    assert defaults.trail_activate_atr_mult == 1.5
    assert defaults.trail_atr_mult == 1.2


def test_atr_trail_activation_and_never_loosen():
    """Trail activates at +1.5×ATR; extreme updates; trail stop never loosens."""
    # ATR=4 → activate +6, profit +10, trail dist 4.8 — room to ratchet without booking.
    policy = AtrTrailScaleOutPolicy()
    state: dict = {"activated": False, "extreme": None, "trail_stop": None}

    hold = atr_trail_scale_out_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=105.00,  # +5 < +6 activate
        policy=policy,
        atr14=4.0,
        trail_state=state,
    )
    assert hold["action"] == "none"
    assert state["activated"] is False

    act = atr_trail_scale_out_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=106.00,  # +6 = 1.5×ATR → activate; trail_stop=106−4.8=101.2
        policy=policy,
        atr14=4.0,
        trail_state=state,
    )
    assert act["action"] == "none"
    assert state["activated"] is True
    assert state["extreme"] == pytest.approx(106.0)
    assert state["trail_stop"] == pytest.approx(101.2)

    # New high still below profit (+10) → extreme + trail ratchet up
    higher = atr_trail_scale_out_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=108.00,
        policy=policy,
        atr14=4.0,
        trail_state=state,
    )
    assert higher["action"] == "none"
    assert state["extreme"] == pytest.approx(108.00)
    assert state["trail_stop"] == pytest.approx(103.2)  # 108 − 4.8
    stop_after_high = float(state["trail_stop"])

    # Pullback that does not touch trail — trail must not loosen
    pullback = atr_trail_scale_out_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=106.50,
        policy=policy,
        atr14=4.0,
        trail_state=state,
    )
    assert pullback["action"] == "none"
    assert state["extreme"] == pytest.approx(108.00)
    assert state["trail_stop"] == pytest.approx(stop_after_high)

    # Touch trail → atr_trail full exit
    touch = atr_trail_scale_out_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=103.20,  # ≤ trail_stop 103.2
        policy=policy,
        atr14=4.0,
        trail_state=state,
    )
    assert touch["action"] == "atr_trail"
    assert touch["exit_qty"] == 1
    assert "atr_trail" in touch["reason"]


def test_atr_trail_qty1_profit_book_and_scale_out():
    """qty=1 at +2.5×ATR → full profit_book; qty>1 → 50% profit_scale_out."""
    policy = AtrTrailScaleOutPolicy()
    # entry 100, ATR=2 → profit at +5.0 (2.5×ATR); tiny +3 must NOT book
    tiny = atr_trail_scale_out_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=103.00,
        policy=policy,
        atr14=2.0,
        trail_state={"activated": False, "extreme": None, "trail_stop": None},
    )
    assert tiny["action"] == "none"

    qty1 = atr_trail_scale_out_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=105.00,
        policy=policy,
        atr14=2.0,
        trail_state={"activated": False, "extreme": None, "trail_stop": None},
    )
    assert qty1["action"] == "profit_book"
    assert qty1["exit_qty"] == 1
    assert "qty=1 full exit profit_book" in qty1["reason"]

    qty4 = atr_trail_scale_out_decision(
        open_side="long",
        open_qty=4,
        entry_avg=100.0,
        mark=105.00,
        policy=policy,
        atr14=2.0,
        trail_state={"activated": False, "extreme": None, "trail_stop": None},
    )
    assert qty4["action"] == "profit_scale_out"
    assert qty4["exit_qty"] == 2

    intents = plan_equity_intents(
        "APOLLOHOSP",
        SignalAction.EXIT,
        open_side="long",
        open_qty=4,
        qty=1,
        price=105.0,
        reason=qty4["reason"],
        exit_qty=qty4["exit_qty"],
    )
    assert intents[0].qty == 2


def test_risk_exit_window_aligns_soft_flatten_until():
    """Open-book risk exits: [09:15, 15:40] IST; entries stay [09:15, 11:30)."""
    policy = SoftEodPolicy(
        open_ist="09:15",
        no_new_entries_after="11:30",
        soft_flatten_ist="15:20",
        soft_flatten_until_ist="15:40",
    )
    # Wed 2026-11-18 00:05 IST — TATACONSUM profit_book bug (cash closed)
    midnight = datetime(2026, 11, 18, 0, 5, 0)
    assert policy.before_session_open(midnight) is True
    assert policy.in_risk_exit_window(midnight) is False
    assert allow_open_book_risk_exits(midnight, policy=policy, trading_day=True) is False
    assert is_trading_session(midnight, policy=policy, trading_day=True) is False

    preopen = datetime(2026, 11, 18, 9, 14, 0)
    assert allow_open_book_risk_exits(preopen, policy=policy, trading_day=True) is False

    open_exact = datetime(2026, 11, 18, 9, 15, 0)
    assert policy.in_risk_exit_window(open_exact) is True
    assert allow_open_book_risk_exits(open_exact, policy=policy, trading_day=True) is True
    assert is_trading_session(open_exact, policy=policy, trading_day=True) is True

    mid = datetime(2026, 11, 18, 12, 0, 0)
    assert is_trading_session(mid, policy=policy, trading_day=True) is False
    assert allow_open_book_risk_exits(mid, policy=policy, trading_day=True) is True

    # After entry cutoff but still in risk-exit / soft-flatten window
    soft_start = datetime(2026, 11, 18, 15, 20, 0)
    assert is_trading_session(soft_start, policy=policy, trading_day=True) is False
    assert allow_open_book_risk_exits(soft_start, policy=policy, trading_day=True) is True
    assert policy.in_soft_flatten_window(soft_start) is True

    soft_until = datetime(2026, 11, 18, 15, 40, 0)
    assert allow_open_book_risk_exits(soft_until, policy=policy, trading_day=True) is True
    assert policy.in_soft_flatten_window(soft_until) is True

    after = datetime(2026, 11, 18, 15, 41, 0)
    assert allow_open_book_risk_exits(after, policy=policy, trading_day=True) is False
    assert policy.in_soft_flatten_window(after) is False

    # Holiday / weekend: clock in window still refused
    assert allow_open_book_risk_exits(mid, policy=policy, trading_day=False) is False


def test_profit_book_refused_at_0005_ist_allowed_in_session():
    """Regression: TATACONSUM profit_book at 00:05 IST must not fill overnight."""
    atr_policy = AtrTrailScaleOutPolicy()
    soft = SoftEodPolicy()
    # Profit threshold: entry 100, ATR=2 → +5.0 (2.5×ATR)
    midnight = datetime(2026, 11, 18, 0, 5, 0)
    overnight = atr_trail_scale_out_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=105.00,
        policy=atr_policy,
        atr14=2.0,
        trail_state={"activated": False, "extreme": None, "trail_stop": None},
        now=midnight,
        session_policy=soft,
        trading_day=True,
    )
    assert overnight["action"] == "none"
    assert overnight["exit_qty"] == 0
    assert "outside_risk_exit_window" in overnight["reason"]

    # Same path during cash session — need +2.5×ATR=+5.0 for profit_book
    midday = datetime(2026, 11, 18, 11, 30, 0)
    session_hold = atr_trail_scale_out_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=103.00,  # +3 < +5
        policy=atr_policy,
        atr14=2.0,
        trail_state={"activated": False, "extreme": None, "trail_stop": None},
        now=midday,
        session_policy=soft,
        trading_day=True,
    )
    assert session_hold["action"] == "none"
    session = atr_trail_scale_out_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=105.00,
        policy=atr_policy,
        atr14=2.0,
        trail_state={"activated": False, "extreme": None, "trail_stop": None},
        now=midday,
        session_policy=soft,
        trading_day=True,
    )
    assert session["action"] == "profit_book"
    assert session["exit_qty"] == 1

    # atr_stop also refused overnight
    stop_night = atr_trail_scale_out_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=95.00,  # −5% < −2.0×ATR
        policy=atr_policy,
        atr14=2.0,
        trail_state={"activated": False, "extreme": None, "trail_stop": None},
        now=midnight,
        session_policy=soft,
        trading_day=True,
    )
    assert stop_night["action"] == "none"
    assert "outside_risk_exit_window" in stop_night["reason"]

    # Morning TP also refused outside risk window (even if morning clock matched —
    # 00:05 is outside morning window too; assert risk gate message when forced).
    morning = MorningProfitBookPolicy()
    morning_night = morning_profit_book_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=101.0,
        policy=morning,
        now=midnight,
        atr14=2.0,
        session_policy=soft,
        trading_day=True,
    )
    assert morning_night["action"] == "none"
    assert "outside_risk_exit_window" in morning_night["reason"]


def test_atr_trail_missing_atr_pct_fallback_and_initial_stop():
    """ATR missing → % floors; initial stop at −1.2%; stop beats profit same tick."""
    policy = AtrTrailScaleOutPolicy()
    state: dict = {"activated": False, "extreme": None, "trail_stop": None}

    # −1.2% stop with no ATR: use mark clearly through the floor (float-safe)
    stop = atr_trail_scale_out_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=98.70,  # −1.3% ≤ −1.2%
        policy=policy,
        atr14=None,
        trail_state=state,
    )
    assert stop["action"] == "atr_stop"
    assert stop["thresholds"]["stop_mode"] == "pct_only"
    assert "atr_stop" in stop["reason"]

    # Just above stop (−0.5%) — hold / no activate yet (+0.8% needed)
    hold = atr_trail_scale_out_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=99.50,
        policy=policy,
        atr14=None,
        trail_state={"activated": False, "extreme": None, "trail_stop": None},
    )
    assert hold["action"] == "none"

    # Activate at +0.8% with no ATR; trail dist uses activate_pct fallback too
    # (mark slightly above floor for float safety)
    act_state: dict = {"activated": False, "extreme": None, "trail_stop": None}
    atr_trail_scale_out_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=100.85,
        policy=policy,
        atr14=None,
        trail_state=act_state,
    )
    assert act_state["activated"] is True
    assert act_state["trail_stop"] == pytest.approx(100.05)  # 100.85 − 0.8%

    # Profit book pct fallback +1.2% (mark above floor for float safety)
    book = atr_trail_scale_out_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=101.30,
        policy=policy,
        atr14=None,
        trail_state={"activated": False, "extreme": None, "trail_stop": None},
    )
    assert book["action"] == "profit_book"
    assert book["thresholds"]["profit_mode"] == "pct_only"

    # ATR path initial stop: −2.0×ATR=−4.0 on entry 100
    atr_stop_miss = atr_trail_scale_out_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=97.00,  # −3 < −4 → hold
        policy=policy,
        atr14=2.0,
        trail_state={"activated": False, "extreme": None, "trail_stop": None},
    )
    assert atr_stop_miss["action"] == "none"
    atr_stop = atr_trail_scale_out_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=96.00,
        policy=policy,
        atr14=2.0,
        trail_state={"activated": False, "extreme": None, "trail_stop": None},
    )
    assert atr_stop["action"] == "atr_stop"
    assert atr_stop["thresholds"]["stop_mode"] == "atr"

    # Indices skipped
    idx = atr_trail_scale_out_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=97.00,
        policy=policy,
        atr14=2.0,
        is_index=True,
    )
    assert idx["action"] == "none"
    assert "index_not_equity" in idx["reason"]


def test_atr_trail_priority_stop_over_profit_same_tick():
    """If somehow both bands could apply, stop wins (checked first)."""
    # Pathological: use a custom policy where stop is positive? Not realistic.
    # Instead: activated trail touch at a mark that is also above profit —
    # trail checked before profit. Seed trail_stop above profit mark geometry.
    policy = AtrTrailScaleOutPolicy()
    state = {
        "activated": True,
        "extreme": 110.0,
        "trail_stop": 108.0,  # already tight; mark 108 touches trail and is +profit
    }
    # mark 108 = +8% >> profit 1.5×ATR, but also touches trail
    hit = atr_trail_scale_out_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=108.0,
        policy=policy,
        atr14=2.0,
        trail_state=state,
    )
    assert hit["action"] == "atr_trail"
