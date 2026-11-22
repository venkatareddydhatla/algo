"""Soft EOD flatten + light index market-sentiment gates."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from options_portal_domain.equity import (
    plan_equity_intents,
    profit_book_exit_qty,
    unrealized_pnl_pct,
)
from options_portal_domain.session_gates import (
    AlignedActionTrendExitPolicy,
    AtrTrailScaleOutPolicy,
    FixedPctExitPolicy,
    MarketSentimentPolicy,
    MorningProfitBookPolicy,
    OppositePineExitPolicy,
    ProfitCandleProtectPolicy,
    SoftEodPolicy,
    aligned_action_trend_decision,
    allow_open_book_risk_exits,
    at_or_after_ist,
    atr_trail_scale_out_decision,
    compute_index_bias,
    entry_conflicts_with_index,
    filter_entry_intents,
    fixed_pct_exit_decision,
    is_cash_market_open,
    is_trading_session,
    morning_profit_book_decision,
    opposite_pine_exit_decision,
    overnight_hold_allowed,
    profit_candle_protect_decision,
    soft_flatten_decision,
)
from options_portal_domain.types import Candle, SignalAction


def test_at_or_after_ist_boundary():
    noon = datetime(2026, 11, 15, 12, 0, 0)
    assert at_or_after_ist("15:20", noon) is False
    assert at_or_after_ist("15:20", datetime(2026, 11, 15, 15, 20, 0)) is True
    assert at_or_after_ist("15:20", datetime(2026, 11, 15, 15, 40, 0)) is True


def test_allow_new_entries_blocks_night_and_preopen():
    """Regression: past_no_new_entries alone allowed 00:01 IST entries."""
    # Explicit entries_open=09:15 keeps this dual-session regression on cash open.
    policy = SoftEodPolicy(
        open_ist="09:15", entries_open_ist="09:15", no_new_entries_after="11:30"
    )
    # Monday 2026-11-16 — trading day; midnight must NOT allow entries
    midnight = datetime(2026, 11, 17, 0, 1, 0)  # Tue 00:01 IST (MUTHOOTFIN bug)
    assert policy.past_no_new_entries(midnight) is False  # old bug path
    assert policy.before_session_open(midnight) is True
    assert policy.before_entries_open(midnight) is True
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


def test_entries_open_0930_blocks_first_15m_keeps_risk_exits():
    """Default: no new entries before 09:30 IST; ±2% risk exits from 09:15 OK."""
    policy = SoftEodPolicy()  # entries_open_ist=09:30, open_ist=09:15
    assert policy.entries_open_ist == "09:30"
    assert policy.open_ist == "09:15"
    assert policy.no_new_entries_after == "15:15"

    cash_open = datetime(2026, 11, 20, 9, 15, 0)
    assert policy.before_session_open(cash_open) is False
    assert policy.before_entries_open(cash_open) is True
    assert policy.allow_new_entries(cash_open) is False
    assert is_trading_session(cash_open, policy=policy, trading_day=True) is False
    assert allow_open_book_risk_exits(cash_open, policy=policy, trading_day=True) is True

    mid_first_bar = datetime(2026, 11, 20, 9, 29, 0)
    assert policy.allow_new_entries(mid_first_bar) is False
    assert allow_open_book_risk_exits(
        mid_first_bar, policy=policy, trading_day=True
    ) is True

    entries_open = datetime(2026, 11, 20, 9, 30, 0)
    assert policy.before_entries_open(entries_open) is False
    assert policy.allow_new_entries(entries_open) is True
    assert is_trading_session(entries_open, policy=policy, trading_day=True) is True

    cutoff = datetime(2026, 11, 20, 15, 15, 0)
    assert policy.allow_new_entries(cutoff) is False
    assert allow_open_book_risk_exits(cutoff, policy=policy, trading_day=True) is True

    from_cfg = SoftEodPolicy.from_config(
        {
            "trading": {
                "session": {
                    "open": "09:15",
                    "entries_open": "09:30",
                    "no_new_entries_after": "15:15",
                }
            }
        }
    )
    assert from_cfg.entries_open_ist == "09:30"
    assert from_cfg.allow_new_entries(cash_open) is False
    assert from_cfg.allow_new_entries(entries_open) is True


def test_dual_session_entry_cutoff_1130_keeps_risk_exits():
    """Entries stop at 11:30; ATR/morning risk exits and soft flatten still allowed."""
    policy = SoftEodPolicy.from_config(
        {
            "trading": {
                "session": {
                    "open": "09:15",
                    "entries_open": "09:15",
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
                    "no_new_entries_after": "15:15",
                    "soft_flatten_ist": "15:15",
                    "soft_flatten_until_ist": "15:30",
                    "overnight_hold_enabled": False,
                    "overnight_hold_min_hybrid_abs": 0.70,
                }
            }
        }
    )
    assert policy.close_ist == "15:30"
    assert policy.soft_flatten_ist == "15:15"
    assert policy.soft_flatten_until_ist == "15:30"
    assert policy.overnight_hold_enabled is False
    assert policy.overnight_hold_min_hybrid_abs == 0.70
    assert "15:15" in policy.user_hours_note
    assert policy.past_soft_flatten(datetime(2026, 11, 15, 15, 14, 0)) is False
    assert policy.past_soft_flatten(datetime(2026, 11, 15, 15, 15, 0)) is True
    assert policy.in_soft_flatten_window(datetime(2026, 11, 15, 15, 15, 0)) is True
    assert policy.in_soft_flatten_window(datetime(2026, 11, 15, 15, 30, 0)) is True
    assert policy.in_soft_flatten_window(datetime(2026, 11, 15, 15, 31, 0)) is False
    assert policy.past_soft_flatten_until(datetime(2026, 11, 15, 17, 13, 0)) is True


def test_overnight_hold_same_side_conviction():
    assert overnight_hold_allowed("long", 0.75, min_abs=0.70) is True
    assert overnight_hold_allowed("long", 0.50, min_abs=0.70) is False
    assert overnight_hold_allowed("long", -0.90, min_abs=0.70) is False
    assert overnight_hold_allowed("short", -0.80, min_abs=0.70) is True
    assert overnight_hold_allowed("short", 0.80, min_abs=0.70) is False
    assert overnight_hold_allowed(None, 0.99, min_abs=0.70) is False
    # Trend must be confirmed when caller passes False
    assert (
        overnight_hold_allowed("long", 0.90, min_abs=0.85, trend_confirmed=False)
        is False
    )
    assert (
        overnight_hold_allowed("long", 0.90, min_abs=0.85, trend_confirmed=True)
        is True
    )


def test_soft_eod_policy_high_conviction_defaults():
    """Defaults: entries 09:30→15:15, flatten ALL from 15:15, overnight off."""
    policy = SoftEodPolicy()
    assert policy.entries_open_ist == "09:30"
    assert policy.no_new_entries_after == "15:15"
    assert policy.soft_flatten_ist == "15:15"
    assert policy.soft_flatten_until_ist == "15:30"
    assert policy.overnight_hold_enabled is False
    assert policy.overnight_hold_min_hybrid_abs == 0.85
    assert policy.past_soft_flatten(datetime(2026, 11, 15, 15, 14, 0)) is False
    assert policy.past_soft_flatten(datetime(2026, 11, 15, 15, 15, 0)) is True


def test_soft_flatten_closes_without_conviction():
    policy = SoftEodPolicy(
        soft_flatten_ist="15:15",
        overnight_hold_enabled=True,
        overnight_hold_min_hybrid_abs=0.70,
    )
    now = datetime(2026, 11, 15, 15, 20, 0)
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
    policy = SoftEodPolicy(
        soft_flatten_ist="15:15",
        overnight_hold_enabled=True,
        overnight_hold_min_hybrid_abs=0.70,
    )
    now = datetime(2026, 11, 15, 15, 20, 0)
    decision = soft_flatten_decision(
        open_side="short",
        open_qty=1,
        hybrid_score=-0.85,
        policy=policy,
        now=now,
    )
    assert decision["action"] == "overnight_hold"
    assert "conviction" in decision["reason"]


def test_soft_flatten_all_when_overnight_disabled():
    """Default Venkata rule: flatten ALL from 15:15 (no overnight hold)."""
    policy = SoftEodPolicy()  # overnight_hold_enabled=False
    now = datetime(2026, 11, 15, 15, 15, 0)
    decision = soft_flatten_decision(
        open_side="long",
        open_qty=1,
        hybrid_score=0.99,
        policy=policy,
        now=now,
        trend_confirmed=True,
    )
    assert decision["action"] == "flatten"
    assert "flatten ALL" in decision["reason"]


def test_soft_flatten_window_end_still_conviction_based():
    """When overnight enabled, soft_flatten_until is still conviction-based."""
    policy = SoftEodPolicy(
        soft_flatten_ist="15:15",
        soft_flatten_until_ist="15:30",
        overnight_hold_enabled=True,
        overnight_hold_min_hybrid_abs=0.70,
    )
    late = datetime(2026, 11, 15, 15, 30, 0)
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
        entries_open_ist="09:15",  # dual-session regression (prod default is 09:30)
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

    # Profit book: min_rrr 1.2 raises pct floor (stop 1.2% → need ≥1.44%)
    book = atr_trail_scale_out_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=101.50,
        policy=policy,
        atr14=None,
        trail_state={"activated": False, "extreme": None, "trail_stop": None},
    )
    assert book["action"] == "profit_book"
    assert "pct_only" in str(book["thresholds"]["profit_mode"])

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


def test_fixed_pct_exit_policy_from_config():
    policy = FixedPctExitPolicy.from_config(
        {
            "trading": {
                "risk": {
                    "fixed_pct_exit": {
                        "enabled": True,
                        "profit_pct": 2.0,
                        "stop_pct": 2.0,
                    }
                }
            }
        }
    )
    assert policy.enabled is True
    assert policy.profit_pct == 2.0
    assert policy.stop_pct == 2.0
    assert policy.reason_tags() == ("pct_profit_2", "pct_stop_2")

    disabled = FixedPctExitPolicy.from_config(
        {"trading": {"risk": {"fixed_pct_exit": {"enabled": False}}}}
    )
    assert disabled.enabled is False


def test_fixed_pct_exit_long_profit_and_stop():
    """Long: mark≥entry×1.02 → pct_profit_2; mark≤entry×0.98 → pct_stop_2."""
    policy = FixedPctExitPolicy()
    session = SoftEodPolicy()
    now = datetime(2026, 11, 20, 11, 0, 0)

    hold = fixed_pct_exit_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=101.0,
        policy=policy,
        now=now,
        trading_day=True,
        session_policy=session,
    )
    assert hold["action"] == "none"
    assert hold["exit_qty"] == 0

    profit = fixed_pct_exit_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=102.0,
        policy=policy,
        now=now,
        trading_day=True,
        session_policy=session,
    )
    assert profit["action"] == "pct_profit_2"
    assert profit["exit_qty"] == 1
    assert "pct_profit_2" in profit["reason"]
    assert profit["unrealized_pct"] == pytest.approx(2.0)

    stop = fixed_pct_exit_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=98.0,
        policy=policy,
        now=now,
        trading_day=True,
        session_policy=session,
    )
    assert stop["action"] == "pct_stop_2"
    assert stop["exit_qty"] == 1
    assert "pct_stop_2" in stop["reason"]
    assert stop["unrealized_pct"] == pytest.approx(-2.0)


def test_fixed_pct_exit_short_profit_and_stop():
    """Short: mark≤entry×0.98 → profit; mark≥entry×1.02 → stop."""
    policy = FixedPctExitPolicy()
    now = datetime(2026, 11, 20, 11, 0, 0)

    profit = fixed_pct_exit_decision(
        open_side="short",
        open_qty=1,
        entry_avg=100.0,
        mark=98.0,
        policy=policy,
        now=now,
        trading_day=True,
    )
    assert profit["action"] == "pct_profit_2"
    assert profit["unrealized_pct"] == pytest.approx(2.0)

    stop = fixed_pct_exit_decision(
        open_side="short",
        open_qty=1,
        entry_avg=100.0,
        mark=102.0,
        policy=policy,
        now=now,
        trading_day=True,
    )
    assert stop["action"] == "pct_stop_2"
    assert stop["unrealized_pct"] == pytest.approx(-2.0)


def test_fixed_pct_exit_not_entry_filter_and_gates():
    """No open book → none; index blocked; disabled; overnight refused."""
    policy = FixedPctExitPolicy()
    empty = fixed_pct_exit_decision(
        open_side=None,
        open_qty=0,
        entry_avg=100.0,
        mark=105.0,
        policy=policy,
    )
    assert empty["action"] == "none"
    assert "no open position" in empty["reason"]

    idx = fixed_pct_exit_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=105.0,
        policy=policy,
        is_index=True,
    )
    assert idx["action"] == "none"
    assert "index_not_equity" in idx["reason"]

    off = fixed_pct_exit_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=105.0,
        policy=FixedPctExitPolicy(enabled=False),
    )
    assert off["action"] == "none"
    assert "disabled" in off["reason"]

    night = fixed_pct_exit_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=105.0,
        policy=policy,
        now=datetime(2026, 11, 20, 0, 5, 0),
        trading_day=True,
    )
    assert night["action"] == "none"
    assert "outside_risk_exit_window" in night["reason"]


def test_fixed_pct_exit_stop_priority_over_profit_impossible_same_mark():
    """Boundary: exact ±2% triggers; interior holds (open-book only)."""
    policy = FixedPctExitPolicy()
    # Just inside band
    inside = fixed_pct_exit_decision(
        open_side="long",
        open_qty=2,
        entry_avg=200.0,
        mark=203.9,  # +1.95%
        policy=policy,
    )
    assert inside["action"] == "none"
    # Exact +2%
    edge = fixed_pct_exit_decision(
        open_side="long",
        open_qty=2,
        entry_avg=200.0,
        mark=204.0,
        policy=policy,
    )
    assert edge["action"] == "pct_profit_2"
    assert edge["exit_qty"] == 2


def _two_bars(
    *,
    prior_h: float,
    prior_l: float,
    cur_o: float,
    cur_c: float,
    prior_c: float | None = None,
    cur_h: float | None = None,
    cur_l: float | None = None,
) -> list[Candle]:
    """Minimal prior + current 15m candles for profit-candle tests."""
    pc = prior_c if prior_c is not None else (prior_h + prior_l) / 2.0
    ch = cur_h if cur_h is not None else max(cur_o, cur_c)
    cl = cur_l if cur_l is not None else min(cur_o, cur_c)
    ts0 = datetime(2026, 11, 20, 10, 0, 0)
    ts1 = datetime(2026, 11, 20, 10, 15, 0)
    return [
        Candle(
            symbol="TEST",
            timeframe="15m",
            ts=ts0,
            open=pc,
            high=prior_h,
            low=prior_l,
            close=pc,
            volume=1000,
        ),
        Candle(
            symbol="TEST",
            timeframe="15m",
            ts=ts1,
            open=cur_o,
            high=ch,
            low=cl,
            close=cur_c,
            volume=1000,
        ),
    ]


def test_profit_candle_protect_policy_from_config():
    on = ProfitCandleProtectPolicy.from_config(
        {"trading": {"risk": {"profit_candle_protect": {"enabled": True}}}}
    )
    assert on.enabled is True
    off = ProfitCandleProtectPolicy.from_config(
        {"trading": {"risk": {"profit_candle_protect": {"enabled": False}}}}
    )
    assert off.enabled is False
    default = ProfitCandleProtectPolicy.from_config({"trading": {"risk": {}}})
    assert default.enabled is True


def test_profit_candle_protect_long_fires_when_open_below_mid_close_below_prior_low():
    """Long in profit: open < mid(prior) and close < prior.low → exit."""
    # prior high=110 low=100 → mid=105; current open=104 close=99
    bars = _two_bars(prior_h=110.0, prior_l=100.0, cur_o=104.0, cur_c=99.0)
    hit = profit_candle_protect_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=101.0,  # +1% profit
        candles=bars,
        policy=ProfitCandleProtectPolicy(),
        now=datetime(2026, 11, 20, 11, 0, 0),
        trading_day=True,
    )
    assert hit["action"] == "profit_candle_protect"
    assert hit["exit_qty"] == 1
    assert hit["prior_mid"] == pytest.approx(105.0)
    assert "profit_candle_protect" in hit["reason"]


def test_profit_candle_protect_long_holds_when_not_in_profit_or_structure_ok():
    bars = _two_bars(prior_h=110.0, prior_l=100.0, cur_o=104.0, cur_c=99.0)
    # Not in profit
    flat = profit_candle_protect_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=100.0,
        candles=bars,
        policy=ProfitCandleProtectPolicy(),
        now=datetime(2026, 11, 20, 11, 0, 0),
        trading_day=True,
    )
    assert flat["action"] == "none"
    assert "not in profit" in flat["reason"]

    # In profit but close still above prior low
    ok_bars = _two_bars(prior_h=110.0, prior_l=100.0, cur_o=104.0, cur_c=100.5)
    hold = profit_candle_protect_decision(
        open_side="long",
        open_qty=1,
        entry_avg=100.0,
        mark=101.0,
        candles=ok_bars,
        policy=ProfitCandleProtectPolicy(),
        now=datetime(2026, 11, 20, 11, 0, 0),
        trading_day=True,
    )
    assert hold["action"] == "none"
    assert "structure intact" in hold["reason"]


def test_profit_candle_protect_short_symmetric():
    """Short in profit: open > mid(prior) and close > prior.high → exit."""
    # prior high=100 low=90 → mid=95; current open=96 close=101
    bars = _two_bars(prior_h=100.0, prior_l=90.0, cur_o=96.0, cur_c=101.0)
    hit = profit_candle_protect_decision(
        open_side="short",
        open_qty=1,
        entry_avg=100.0,
        mark=99.0,  # +1% short profit
        candles=bars,
        policy=ProfitCandleProtectPolicy(),
        now=datetime(2026, 11, 20, 11, 0, 0),
        trading_day=True,
    )
    assert hit["action"] == "profit_candle_protect"
    assert hit["exit_qty"] == 1
    assert hit["prior_mid"] == pytest.approx(95.0)

    # Structure not broken (close below prior high)
    hold_bars = _two_bars(prior_h=100.0, prior_l=90.0, cur_o=96.0, cur_c=99.5)
    hold = profit_candle_protect_decision(
        open_side="short",
        open_qty=1,
        entry_avg=100.0,
        mark=99.0,
        candles=hold_bars,
        policy=ProfitCandleProtectPolicy(),
        now=datetime(2026, 11, 20, 11, 0, 0),
        trading_day=True,
    )
    assert hold["action"] == "none"


def test_opposite_pine_exit_policy_from_config():
    on = OppositePineExitPolicy.from_config(
        {"trading": {"risk": {"opposite_pine_exit": {"enabled": True}}}}
    )
    assert on.enabled is True
    off = OppositePineExitPolicy.from_config(
        {"trading": {"risk": {"opposite_pine_exit": {"enabled": False}}}}
    )
    assert off.enabled is False
    default = OppositePineExitPolicy.from_config({"trading": {"risk": {}}})
    assert default.enabled is True


def test_opposite_pine_long_closes_on_fresh_sell():
    """Open LONG + fresh Pine SELL → opposite_pine_sell (close-only)."""
    bar = datetime(2026, 11, 20, 5, 0, 0, tzinfo=timezone.utc)  # 10:30 IST
    now = bar + timedelta(minutes=7)
    hit = opposite_pine_exit_decision(
        open_side="long",
        open_qty=1,
        pine_action=SignalAction.SELL,
        bar_ts=bar,
        policy=OppositePineExitPolicy(),
        now=now,
    )
    assert hit["action"] == "opposite_pine_sell"
    assert hit["exit_qty"] == 1
    assert hit["fresh_action"] == "SELL"
    assert "opposite_pine_sell" in hit["reason"]

    # Planner close-only: EXIT → sell to close long (no flip_open).
    intents = plan_equity_intents(
        "RELIANCE",
        SignalAction.EXIT,
        open_side="long",
        open_qty=1,
        qty=1,
        price=2500.0,
        reason=str(hit["reason"]),
    )
    assert len(intents) == 1
    assert intents[0].action_kind == "exit"
    assert intents[0].side == "SELL"


def test_opposite_pine_short_covers_on_fresh_buy():
    """Open SHORT + fresh Pine BUY → opposite_pine_buy (cover)."""
    bar = datetime(2026, 11, 20, 5, 15, 0, tzinfo=timezone.utc)
    now = bar + timedelta(minutes=5)
    hit = opposite_pine_exit_decision(
        open_side="short",
        open_qty=1,
        pine_action=SignalAction.BUY,
        bar_ts=bar,
        policy=OppositePineExitPolicy(),
        now=now,
    )
    assert hit["action"] == "opposite_pine_buy"
    assert hit["exit_qty"] == 1
    assert hit["fresh_action"] == "BUY"

    intents = plan_equity_intents(
        "TCS",
        SignalAction.EXIT,
        open_side="short",
        open_qty=1,
        qty=1,
        price=3900.0,
        reason=str(hit["reason"]),
    )
    assert len(intents) == 1
    assert intents[0].action_kind == "exit"
    assert intents[0].side == "BUY"


def test_opposite_pine_no_close_on_hold():
    bar = datetime(2026, 11, 20, 5, 0, 0, tzinfo=timezone.utc)
    hold = opposite_pine_exit_decision(
        open_side="long",
        open_qty=1,
        pine_action=SignalAction.HOLD,
        bar_ts=bar,
        policy=OppositePineExitPolicy(),
        now=bar,
    )
    assert hold["action"] == "none"
    assert hold["fresh_action"] == "HOLD"
    assert hold["exit_qty"] == 0


def test_opposite_pine_no_close_on_stale_expired_action():
    """Stale SELL hours later expires → HOLD; must not re-close."""
    bar = datetime(2026, 11, 20, 4, 0, 0, tzinfo=timezone.utc)  # morning signal
    hours_later = bar + timedelta(hours=4)
    stale = opposite_pine_exit_decision(
        open_side="long",
        open_qty=1,
        pine_action=SignalAction.SELL,
        bar_ts=bar,
        policy=OppositePineExitPolicy(),
        now=hours_later,
    )
    assert stale["action"] == "none"
    assert stale["fresh_action"] == "HOLD"
    assert "expired" in stale["reason"] or "HOLD" in stale["reason"]

    # Missing bar_ts also counts as stale / not current.
    missing = opposite_pine_exit_decision(
        open_side="short",
        open_qty=1,
        pine_action="BUY",
        bar_ts=None,
        policy=OppositePineExitPolicy(),
        now=hours_later,
    )
    assert missing["action"] == "none"
    assert missing["fresh_action"] == "HOLD"


def test_opposite_pine_same_side_does_not_close():
    bar = datetime(2026, 11, 20, 5, 0, 0, tzinfo=timezone.utc)
    same = opposite_pine_exit_decision(
        open_side="long",
        open_qty=1,
        pine_action=SignalAction.BUY,
        bar_ts=bar,
        policy=OppositePineExitPolicy(),
        now=bar,
    )
    assert same["action"] == "none"
    assert same["fresh_action"] == "BUY"


def test_aligned_action_trend_policy_from_config():
    on = AlignedActionTrendExitPolicy.from_config(
        {
            "trading": {
                "risk": {
                    "aligned_action_trend_exit": {"enabled": True, "allow_flip": True}
                }
            }
        }
    )
    assert on.enabled is True and on.allow_flip is True
    off = AlignedActionTrendExitPolicy.from_config(
        {"trading": {"risk": {"aligned_action_trend_exit": {"enabled": False}}}}
    )
    assert off.enabled is False
    default = AlignedActionTrendExitPolicy.from_config({"trading": {"risk": {}}})
    assert default.enabled is True and default.allow_flip is True


def test_aligned_long_flips_on_sell_bearish():
    """Open LONG + SELL+BEARISH → close long and open short."""
    hit = aligned_action_trend_decision(
        open_side="long",
        open_qty=1,
        pine_action=SignalAction.SELL,
        trend="BEARISH",
        policy=AlignedActionTrendExitPolicy(),
    )
    assert hit["action"] == "aligned_opposite_sell"
    assert hit["flip"] is True
    assert hit["signal_action"] == "SELL"
    assert hit["exit_qty"] == 1

    intents = plan_equity_intents(
        "RELIANCE",
        SignalAction.SELL,
        open_side="long",
        open_qty=1,
        qty=1,
        price=2500.0,
        reason=str(hit["reason"]),
    )
    kinds = [i.action_kind for i in intents]
    assert kinds == ["flip_close", "flip_open"]
    assert intents[0].side == "SELL"
    assert intents[1].side == "SELL"


def test_aligned_short_flips_on_buy_bullish():
    """Open SHORT + BUY+BULLISH → cover short and open long."""
    hit = aligned_action_trend_decision(
        open_side="short",
        open_qty=1,
        pine_action="BUY",
        trend="BULLISH",
        policy=AlignedActionTrendExitPolicy(),
    )
    assert hit["action"] == "aligned_opposite_buy"
    assert hit["flip"] is True
    assert hit["signal_action"] == "BUY"

    intents = plan_equity_intents(
        "TCS",
        SignalAction.BUY,
        open_side="short",
        open_qty=1,
        qty=1,
        price=3900.0,
        reason=str(hit["reason"]),
    )
    assert [i.action_kind for i in intents] == ["flip_close", "flip_open"]


def test_aligned_neutral_closes_no_reverse():
    hit = aligned_action_trend_decision(
        open_side="long",
        open_qty=1,
        pine_action=SignalAction.BUY,
        trend="NEUTRAL",
        policy=AlignedActionTrendExitPolicy(),
    )
    assert hit["action"] == "aligned_neutral_exit"
    assert hit["flip"] is False
    assert hit["signal_action"] == "EXIT"

    intents = plan_equity_intents(
        "INFY",
        SignalAction.EXIT,
        open_side="long",
        open_qty=1,
        qty=1,
        price=1500.0,
        reason=str(hit["reason"]),
    )
    assert len(intents) == 1
    assert intents[0].action_kind == "exit"


def test_aligned_misaligned_holds_open():
    """BUY+BEARISH while long → no aligned exit (wait for NEUTRAL / opposite)."""
    hit = aligned_action_trend_decision(
        open_side="long",
        open_qty=1,
        pine_action=SignalAction.BUY,
        trend="BEARISH",
        policy=AlignedActionTrendExitPolicy(),
    )
    assert hit["action"] == "none"
    assert hit["exit_qty"] == 0


def test_aligned_same_side_holds_one_open_max():
    hit = aligned_action_trend_decision(
        open_side="long",
        open_qty=1,
        pine_action=SignalAction.BUY,
        trend="BULLISH",
        policy=AlignedActionTrendExitPolicy(),
    )
    assert hit["action"] == "none"
    assert "already BUY+BULLISH" in hit["reason"]


def test_aligned_close_only_when_flip_disabled():
    hit = aligned_action_trend_decision(
        open_side="long",
        open_qty=1,
        pine_action=SignalAction.SELL,
        trend="BEARISH",
        policy=AlignedActionTrendExitPolicy(allow_flip=False),
    )
    assert hit["action"] == "aligned_opposite_sell"
    assert hit["flip"] is False
    assert hit["signal_action"] == "EXIT"
