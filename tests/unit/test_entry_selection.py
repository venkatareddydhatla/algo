"""Unit tests for |hybrid|-ranked equity entry selection (anti-churn + dynamic)."""

from __future__ import annotations

from datetime import datetime

import pytest

from options_portal_domain.entry_selection import (
    EntryCandidate,
    count_morning_entries_from_fills,
    dynamic_target_open,
    effective_min_hybrid_abs,
    entry_selection_config,
    free_open_slots,
    in_morning_entry_window,
    in_reentry_cooloff,
    last_exit_times_from_fills,
    morning_entry_slots_remaining,
    rank_entry_candidates,
    select_entries_for_empty_slots,
    symbols_in_reentry_cooloff,
)
from options_portal_domain.tz import ist


def test_rank_by_abs_hybrid_strongest_first():
    rows = [
        EntryCandidate("AAA", 0.46, 0.5),
        EntryCandidate("BBB", -0.90, 0.4),
        EntryCandidate("CCC", 0.70, 0.9),
        EntryCandidate("DDD", 0.70, 0.5),  # same |h| as CCC → higher conf wins
    ]
    ranked = rank_entry_candidates(rows)
    assert [c.symbol for c in ranked] == ["BBB", "CCC", "DDD", "AAA"]


def test_select_fills_empty_slots_only_keeps_opens():
    """Anti-churn: existing opens never rotated for a stronger candidate."""
    opens = ["KEEP1", "KEEP2", "KEEP3"]
    candidates = [
        EntryCandidate("WEAK", 0.46),
        EntryCandidate("STRONG", -0.92),
        EntryCandidate("MID", 0.70),
        EntryCandidate("KEEP1", 0.99),  # already open — ignored
        EntryCandidate("LATE", 0.55),
    ]
    # max 5 → 2 free slots → STRONG, MID (not WEAK/LATE by scan order)
    picked = select_entries_for_empty_slots(
        candidates, open_symbols=opens, max_open_symbols=5
    )
    assert [c.symbol for c in picked] == ["STRONG", "MID"]
    assert free_open_slots(opens, 5) == 2


def test_select_no_slots_when_book_full():
    opens = [f"S{i}" for i in range(10)]
    picked = select_entries_for_empty_slots(
        [EntryCandidate("BEST", 0.99)],
        open_symbols=opens,
        max_open_symbols=10,
    )
    assert picked == []


def test_select_top_n_from_scratch_not_csv_order():
    """Empty book: strongest |hybrid| win, not alphabetical / CSV order."""
    candidates = [
        EntryCandidate("360ONE", 0.46),
        EntryCandidate("ABB", 0.50),
        EntryCandidate("ZOMATO", -0.88),
        EntryCandidate("RELIANCE", 0.72),
        EntryCandidate("TCS", 0.60),
    ]
    picked = select_entries_for_empty_slots(
        candidates, open_symbols=[], max_open_symbols=3
    )
    assert [c.symbol for c in picked] == ["ZOMATO", "RELIANCE", "TCS"]


def test_dedupe_keeps_stronger_hybrid():
    candidates = [
        EntryCandidate("RELIANCE", 0.50, 0.4),
        EntryCandidate("reliance", 0.80, 0.6),
    ]
    picked = select_entries_for_empty_slots(
        candidates, open_symbols=[], max_open_symbols=10
    )
    assert len(picked) == 1
    assert picked[0].symbol == "RELIANCE"
    assert picked[0].hybrid_score == 0.80


def test_dynamic_target_clamps_floor_ceiling():
    assert dynamic_target_open(open_count=0, qualifying_new=20, floor=4, ceiling=8) == 8
    assert dynamic_target_open(open_count=2, qualifying_new=3, floor=4, ceiling=8) == 5
    # Below floor when not enough qualify — do not invent trades
    assert dynamic_target_open(open_count=0, qualifying_new=2, floor=4, ceiling=8) == 2
    assert dynamic_target_open(open_count=6, qualifying_new=10, floor=4, ceiling=8) == 8


def test_dynamic_admits_only_above_threshold_strongest_first():
    candidates = [
        EntryCandidate("WEAK", 0.46),  # below 0.65
        EntryCandidate("STRONG", -0.92),
        EntryCandidate("MID", 0.70),
        EntryCandidate("EDGE", 0.65),
        EntryCandidate("LOW", 0.64),
    ]
    picked = select_entries_for_empty_slots(
        candidates,
        open_symbols=[],
        max_open_symbols=8,
        dynamic=True,
        floor=4,
        ceiling=8,
        min_hybrid_abs=0.65,
    )
    assert [c.symbol for c in picked] == ["STRONG", "MID", "EDGE"]
    # 3 qualify < floor 4 → admit all 3 (no weak fillers)


def test_dynamic_fills_toward_ceiling_with_opens():
    opens = ["KEEP1", "KEEP2"]
    candidates = [
        EntryCandidate(f"S{i}", 0.66 + i * 0.01) for i in range(20)
    ]
    picked = select_entries_for_empty_slots(
        candidates,
        open_symbols=opens,
        max_open_symbols=8,
        dynamic=True,
        floor=4,
        ceiling=8,
        min_hybrid_abs=0.65,
    )
    # target = clamp(2+20, 4, 8) = 8 → 6 free slots; strongest first
    assert len(picked) == 6
    assert picked[0].symbol == "S19"
    assert "KEEP1" not in {c.symbol for c in picked}


def test_dynamic_anti_churn_never_rotates_opens():
    opens = ["KEEP1", "KEEP2", "KEEP3", "KEEP4"]
    candidates = [
        EntryCandidate("STRONG", 0.99),
        EntryCandidate("KEEP1", 0.50),  # already open
    ]
    picked = select_entries_for_empty_slots(
        candidates,
        open_symbols=opens,
        max_open_symbols=8,
        dynamic=True,
        floor=4,
        ceiling=8,
        min_hybrid_abs=0.65,
    )
    # target = clamp(4+1, 4, 8) = 5 → 1 free → STRONG only
    assert [c.symbol for c in picked] == ["STRONG"]


def test_cooloff_symbols_blocked_from_admission():
    """Re-entry cooloff: closed symbols cannot fill empty slots."""
    candidates = [
        EntryCandidate("ABCAPITAL", 0.90),
        EntryCandidate("RELIANCE", 0.80),
        EntryCandidate("TCS", 0.75),
    ]
    picked = select_entries_for_empty_slots(
        candidates,
        open_symbols=[],
        max_open_symbols=8,
        dynamic=True,
        floor=3,
        ceiling=8,
        min_hybrid_abs=0.65,
        cooloff_symbols=["ABCAPITAL", "AUBANK"],
    )
    assert [c.symbol for c in picked] == ["RELIANCE", "TCS"]
    assert "ABCAPITAL" not in {c.symbol for c in picked}


def test_in_reentry_cooloff_within_window_same_session():
    z = ist()
    closed = datetime(2026, 11, 17, 11, 23, tzinfo=z)
    now = datetime(2026, 11, 17, 12, 30, tzinfo=z)  # 67 min later
    assert in_reentry_cooloff(
        last_close=closed, now=now, cooloff_minutes=90, same_session_only=True
    )
    later = datetime(2026, 11, 17, 13, 0, tzinfo=z)  # 97 min later
    assert not in_reentry_cooloff(
        last_close=closed, now=later, cooloff_minutes=90, same_session_only=True
    )


def test_in_reentry_cooloff_does_not_cross_session():
    """Prior-session close does not block next morning (same_session_only)."""
    z = ist()
    # Monday close → Tuesday morning (different NSE session day)
    closed = datetime(2026, 11, 16, 15, 25, tzinfo=z)
    now = datetime(2026, 11, 17, 9, 30, tzinfo=z)
    assert not in_reentry_cooloff(
        last_close=closed, now=now, cooloff_minutes=90, same_session_only=True
    )


def test_symbols_in_reentry_cooloff_set():
    z = ist()
    now = datetime(2026, 11, 17, 12, 0, tzinfo=z)
    last = {
        "ABCAPITAL": datetime(2026, 11, 17, 11, 0, tzinfo=z),  # 60m ago → blocked
        "RELIANCE": datetime(2026, 11, 17, 10, 0, tzinfo=z),  # 120m ago → free
        "OLD": datetime(2026, 11, 16, 14, 0, tzinfo=z),  # prior session → free
    }
    blocked = symbols_in_reentry_cooloff(
        last, now=now, cooloff_minutes=90, same_session_only=True
    )
    assert blocked == {"ABCAPITAL"}


def test_last_exit_times_from_fills_keeps_newest_exit():
    fills = [
        {
            "symbol": "AXISBANK",
            "action_kind": "exit",
            "ts_ist": "2026-11-17T11:23:00+05:30",
            "realized_pnl": 1.0,
        },
        {
            "symbol": "AXISBANK",
            "action_kind": "exit",
            "ts_ist": "2026-11-17T13:25:00+05:30",
            "realized_pnl": -0.5,
        },
        {
            "symbol": "AXISBANK",
            "action_kind": "entry",
            "ts_ist": "2026-11-17T14:00:00+05:30",
        },
        {
            "symbol": "TCS",
            "action_kind": "flip_close",
            "ts_ist_display": "2026-11-17 10:15 IST",
            "realized_pnl": 2.0,
        },
    ]
    last = last_exit_times_from_fills(fills)
    assert set(last) == {"AXISBANK", "TCS"}
    assert last["AXISBANK"].hour == 13 and last["AXISBANK"].minute == 25
    assert last["TCS"].hour == 10 and last["TCS"].minute == 15


def test_effective_min_hybrid_afternoon_raise():
    z = ist()
    morning = datetime(2026, 11, 17, 11, 0, tzinfo=z)
    afternoon = datetime(2026, 11, 17, 12, 0, tzinfo=z)
    assert (
        effective_min_hybrid_abs(
            0.72,
            now=morning,
            afternoon_after_ist="12:00",
            afternoon_min_hybrid_abs=0.75,
        )
        == 0.72
    )
    assert (
        effective_min_hybrid_abs(
            0.72,
            now=afternoon,
            afternoon_after_ist="12:00",
            afternoon_min_hybrid_abs=0.75,
        )
        == 0.75
    )


def test_dynamic_afternoon_floor_filters_mid_scores():
    """After noon effective floor 0.75 drops |h|=0.72 candidates."""
    candidates = [
        EntryCandidate("EDGE72", 0.72),
        EntryCandidate("STRONG", -0.90),
        EntryCandidate("MID75", 0.75),
    ]
    # Morning floor 0.72 would admit all three into 5 slots
    morning = select_entries_for_empty_slots(
        candidates,
        open_symbols=[],
        max_open_symbols=5,
        dynamic=True,
        floor=3,
        ceiling=5,
        min_hybrid_abs=0.72,
    )
    assert {c.symbol for c in morning} == {"EDGE72", "STRONG", "MID75"}

    afternoon = select_entries_for_empty_slots(
        candidates,
        open_symbols=[],
        max_open_symbols=5,
        dynamic=True,
        floor=3,
        ceiling=5,
        min_hybrid_abs=0.75,
    )
    assert [c.symbol for c in afternoon] == ["STRONG", "MID75"]


def test_rest_of_day_floor_rejects_065_admits_072():
    """Rest-of-day |hybrid| floor 0.72: 0.65/0.70 fail; 0.72+ pass under ceiling 12."""
    candidates = [
        EntryCandidate("LOW65", 0.65),
        EntryCandidate("MID70", -0.70),
        EntryCandidate("EDGE72", 0.72),
        EntryCandidate("STRONG", -0.88),
        EntryCandidate("WEAK55", 0.55),
    ]
    picked = select_entries_for_empty_slots(
        candidates,
        open_symbols=[],
        max_open_symbols=12,
        dynamic=True,
        floor=3,
        ceiling=12,
        min_hybrid_abs=0.72,
    )
    assert [c.symbol for c in picked] == ["STRONG", "EDGE72"]


def test_ceiling_twelve_empty_slots_only_when_full():
    """Max opens 12 + empty_slots_only: never admit when book already full."""
    opens = [f"S{i}" for i in range(12)]
    picked = select_entries_for_empty_slots(
        [EntryCandidate("STRONG", 0.99)],
        open_symbols=opens,
        max_open_symbols=12,
        dynamic=True,
        floor=3,
        ceiling=12,
        min_hybrid_abs=0.72,
    )
    assert picked == []


def test_morning_max_new_entries_budget_not_refill():
    """Morning max 3 new entries — strongest first; budget is not perpetual refill."""
    candidates = [
        EntryCandidate(f"S{i}", 0.80 + i * 0.01) for i in range(10)
    ]
    picked = select_entries_for_empty_slots(
        candidates,
        open_symbols=[],
        max_open_symbols=12,
        dynamic=True,
        floor=3,
        ceiling=12,
        min_hybrid_abs=0.75,
        max_new_entries=3,
    )
    assert len(picked) == 3
    assert [c.symbol for c in picked] == ["S9", "S8", "S7"]

    # Remaining 0 after 3 used → no more morning admits even with empty slots
    assert morning_entry_slots_remaining(morning_entries_used=3, max_new_entries=3) == 0
    blocked = select_entries_for_empty_slots(
        candidates,
        open_symbols=[],
        max_open_symbols=12,
        dynamic=True,
        floor=3,
        ceiling=12,
        min_hybrid_abs=0.75,
        max_new_entries=0,
    )
    assert blocked == []


def test_morning_floor_075_rejects_072():
    """Morning |hybrid| ≥ 0.75: 0.72 fails; 0.75+ pass."""
    candidates = [
        EntryCandidate("EDGE72", 0.72),
        EntryCandidate("MID75", 0.75),
        EntryCandidate("STRONG", -0.90),
    ]
    picked = select_entries_for_empty_slots(
        candidates,
        open_symbols=[],
        max_open_symbols=12,
        dynamic=True,
        floor=3,
        ceiling=12,
        min_hybrid_abs=0.75,
        max_new_entries=3,
    )
    assert [c.symbol for c in picked] == ["STRONG", "MID75"]


def test_in_morning_entry_window_and_effective_floor():
    z = ist()
    morning = datetime(2026, 11, 18, 9, 40, tzinfo=z)
    rest = datetime(2026, 11, 18, 10, 15, tzinfo=z)
    assert in_morning_entry_window(morning) is True
    assert in_morning_entry_window(rest) is False
    assert (
        effective_min_hybrid_abs(
            0.72,
            now=morning,
            morning_until_ist="10:00",
            morning_min_hybrid_abs=0.75,
        )
        == 0.75
    )
    assert (
        effective_min_hybrid_abs(
            0.72,
            now=rest,
            morning_until_ist="10:00",
            morning_min_hybrid_abs=0.75,
        )
        == 0.72
    )


def test_count_morning_entries_from_fills():
    z = ist()
    now = datetime(2026, 11, 18, 9, 50, tzinfo=z)
    fills = [
        {
            "symbol": "A",
            "action_kind": "entry",
            "ts_ist": "2026-11-18T09:20:00+05:30",
        },
        {
            "symbol": "B",
            "action_kind": "entry",
            "ts_ist": "2026-11-18T09:45:00+05:30",
        },
        {
            "symbol": "C",
            "action_kind": "entry",
            "ts_ist": "2026-11-18T10:15:00+05:30",  # rest-of-day — not morning
        },
        {
            "symbol": "D",
            "action_kind": "exit",
            "ts_ist": "2026-11-18T09:30:00+05:30",
        },
        {
            "symbol": "E",
            "action_kind": "entry",
            "ts_ist": "2026-11-17T09:30:00+05:30",  # prior session
        },
    ]
    assert count_morning_entries_from_fills(fills, now=now) == 2


def test_dual_session_reentry_cooloff_180m():
    """180m cooloff blocks same-session re-entry; clears after window."""
    z = ist()
    closed = datetime(2026, 11, 18, 9, 40, tzinfo=z)
    within = datetime(2026, 11, 18, 12, 30, tzinfo=z)  # 170m later
    assert in_reentry_cooloff(
        last_close=closed, now=within, cooloff_minutes=180, same_session_only=True
    )
    after = datetime(2026, 11, 18, 12, 41, tzinfo=z)  # 181m later
    assert not in_reentry_cooloff(
        last_close=closed, now=after, cooloff_minutes=180, same_session_only=True
    )


def test_entry_selection_config_defaults():
    cfg = {"trading": {"risk": {"max_open_symbols": 6}}}
    sel = entry_selection_config(cfg)
    assert sel["enabled"] is True
    assert sel["rank_by"] == "abs_hybrid"
    assert sel["anti_churn"] == "empty_slots_only"
    assert sel["max_open_symbols"] == 6
    assert sel["dynamic_capacity"]["enabled"] is False
    # Cooloff defaults on even when YAML omits the block (120m multi-factor)
    assert sel["reentry_cooloff"]["enabled"] is True
    assert sel["reentry_cooloff"]["minutes"] == 120
    # Morning budget off by default (high-conviction time floors supersede)
    assert sel["morning_session"]["enabled"] is False
    assert sel["morning_session"]["until_ist"] == "10:00"
    assert sel["morning_session"]["min_hybrid_abs"] == 0.75
    assert sel["dynamic_capacity"]["min_hybrid_abs"] == 0.75
    # Afternoon raise default on when omitted
    assert sel["dynamic_capacity"]["afternoon_after_ist"] == "12:00"
    assert sel["dynamic_capacity"]["afternoon_min_hybrid_abs"] == 0.80


def test_entry_selection_config_dynamic_knobs():
    cfg = {
        "trading": {
            "risk": {
                "max_open_symbols": 6,
                "entry_selection": {
                    "enabled": True,
                    "rank_by": "abs_hybrid",
                    "anti_churn": "empty_slots_only",
                    "reentry_cooloff": {
                        "enabled": True,
                        "minutes": 120,
                        "same_session_only": True,
                    },
                    "morning_session": {
                        "enabled": False,
                        "until_ist": "10:00",
                        "min_hybrid_abs": 0.75,
                        "max_new_entries": 3,
                    },
                    "dynamic_capacity": {
                        "enabled": True,
                        "floor": 2,
                        "ceiling": 6,
                        "min_hybrid_abs": 0.75,
                        "afternoon_after_ist": "12:00",
                        "afternoon_min_hybrid_abs": 0.80,
                    },
                },
            }
        }
    }
    sel = entry_selection_config(cfg)
    dyn = sel["dynamic_capacity"]
    assert dyn["enabled"] is True
    assert dyn["floor"] == 2
    assert dyn["ceiling"] == 6
    assert dyn["min_hybrid_abs"] == 0.75
    assert dyn["afternoon_after_ist"] == "12:00"
    assert dyn["afternoon_min_hybrid_abs"] == 0.80
    assert sel["morning_session"]["enabled"] is False
    assert sel["reentry_cooloff"]["minutes"] == 120
    # ceiling cannot exceed hard cap
    cfg["trading"]["risk"]["entry_selection"]["dynamic_capacity"]["ceiling"] = 99
    sel2 = entry_selection_config(cfg)
    assert sel2["dynamic_capacity"]["ceiling"] == 6


def test_entry_selection_config_can_disable():
    cfg = {
        "trading": {
            "risk": {
                "max_open_symbols": 8,
                "entry_selection": {"enabled": False},
            }
        }
    }
    sel = entry_selection_config(cfg)
    assert sel["enabled"] is False
    assert sel["max_open_symbols"] == 8


def test_entry_selection_config_can_disable_cooloff():
    cfg = {
        "trading": {
            "risk": {
                "max_open_symbols": 8,
                "entry_selection": {
                    "reentry_cooloff": {"enabled": False, "minutes": 30},
                },
            }
        }
    }
    sel = entry_selection_config(cfg)
    assert sel["reentry_cooloff"]["enabled"] is False
    assert sel["reentry_cooloff"]["minutes"] == 30


def _load_worker_entry_admission():
    """Load worker entry_admission without clashing with apps/api ``app``."""
    import importlib.util
    import sys
    from pathlib import Path

    name = "options_portal_worker_entry_admission"
    # Always reload so tests pick up source edits in this session.
    path = Path(__file__).resolve().parents[2] / "apps" / "worker" / "app" / "entry_admission.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_admission_from_config_none_when_disabled():
    EntryCycleAdmission = _load_worker_entry_admission().EntryCycleAdmission

    assert (
        EntryCycleAdmission.from_config(
            {"trading": {"risk": {"entry_selection": {"enabled": False}}}}
        )
        is None
    )
    adm = EntryCycleAdmission.from_config(
        {
            "trading": {
                "risk": {
                    "max_open_symbols": 6,
                    "entry_selection": {
                        "reentry_cooloff": {
                            "enabled": True,
                            "minutes": 120,
                            "same_session_only": True,
                        },
                        "morning_session": {
                            "enabled": False,
                            "until_ist": "10:00",
                            "min_hybrid_abs": 0.75,
                            "max_new_entries": 3,
                        },
                        "dynamic_capacity": {
                            "enabled": True,
                            "floor": 2,
                            "ceiling": 6,
                            "min_hybrid_abs": 0.75,
                            "afternoon_after_ist": "12:00",
                            "afternoon_min_hybrid_abs": 0.80,
                        },
                    },
                }
            }
        }
    )
    assert adm is not None
    assert adm.max_open_symbols == 6
    assert adm.anti_churn == "empty_slots_only"
    assert adm.dynamic_enabled is True
    assert adm.floor == 2
    assert adm.ceiling == 6
    assert adm.min_hybrid_abs == 0.75
    assert adm.cooloff_enabled is True
    assert adm.cooloff_minutes == 120
    assert adm.morning_enabled is False
    assert adm.afternoon_after_ist == "12:00"


def test_admission_effective_min_hybrid_high_conviction_clock():
    """High-conviction floors: 0.75 / 0.80 / 0.85 by IST clock (+ adaptive)."""
    EntryCycleAdmission = _load_worker_entry_admission().EntryCycleAdmission
    adm = EntryCycleAdmission(max_open_symbols=6, min_hybrid_abs=0.75)
    z = ist()
    assert adm._effective_min_hybrid(datetime(2026, 11, 18, 9, 40, tzinfo=z)) == 0.75
    assert adm._effective_min_hybrid(datetime(2026, 11, 18, 12, 15, tzinfo=z)) == 0.80
    assert adm._effective_min_hybrid(datetime(2026, 11, 18, 14, 30, tzinfo=z)) == 0.85
    assert (
        adm._effective_min_hybrid(
            datetime(2026, 11, 18, 10, 0, tzinfo=z), adaptive_delta=0.05
        )
        == 0.80
    )


def test_admission_effective_min_hybrid_legacy_when_hc_disabled():
    EntryCycleAdmission = _load_worker_entry_admission().EntryCycleAdmission
    adm = EntryCycleAdmission(
        max_open_symbols=12,
        min_hybrid_abs=0.72,
        morning_enabled=True,
        morning_until_ist="10:00",
        morning_min_hybrid_abs=0.75,
    )
    z = ist()
    cfg = {"trading": {"risk": {"high_conviction": {"enabled": False}}}}
    assert (
        adm._effective_min_hybrid(
            datetime(2026, 11, 18, 9, 40, tzinfo=z), cfg=cfg
        )
        == 0.75
    )
    assert (
        adm._effective_min_hybrid(
            datetime(2026, 11, 18, 10, 15, tzinfo=z), cfg=cfg
        )
        == 0.72
    )


@pytest.mark.asyncio
async def test_admission_stages_strongest_and_selects():
    mod = _load_worker_entry_admission()
    EntryCycleAdmission = mod.EntryCycleAdmission
    PendingEntry = mod.PendingEntry
    from options_portal_domain.equity import EquityOrderIntent

    def _intent(sym: str) -> EquityOrderIntent:
        return EquityOrderIntent(
            symbol=sym,
            side="BUY",
            qty=1,
            limit_price=100.0,
            reason="test",
            action_kind="entry",
            resulting_position="long",
            signal_action="BUY_CE_BIAS",
        )

    adm = EntryCycleAdmission(max_open_symbols=2)
    await adm.stage(
        PendingEntry(
            symbol="WEAK",
            hybrid_score=0.46,
            confidence=0.5,
            intents=[_intent("WEAK")],
            safe_mode="dry_run",
            limits={"max_open_symbols": 2},
        )
    )
    await adm.stage(
        PendingEntry(
            symbol="STRONG",
            hybrid_score=-0.91,
            confidence=0.8,
            intents=[_intent("STRONG")],
            safe_mode="dry_run",
            limits={"max_open_symbols": 2},
        )
    )
    await adm.stage(
        PendingEntry(
            symbol="MID",
            hybrid_score=0.70,
            confidence=0.6,
            intents=[_intent("MID")],
            safe_mode="dry_run",
            limits={"max_open_symbols": 2},
        )
    )
    cands = adm.pending_candidates()
    picked = select_entries_for_empty_slots(
        cands, open_symbols=["KEEP"], max_open_symbols=2
    )
    # 1 free slot → STRONG only
    assert [c.symbol for c in picked] == ["STRONG"]


@pytest.mark.asyncio
async def test_admission_refuses_index_underlyings():
    """NIFTY/BANKNIFTY/… must never enter deferred equity admission queue."""
    mod = _load_worker_entry_admission()
    EntryCycleAdmission = mod.EntryCycleAdmission
    PendingEntry = mod.PendingEntry
    from options_portal_domain.equity import EquityOrderIntent

    def _intent(sym: str) -> EquityOrderIntent:
        return EquityOrderIntent(
            symbol=sym,
            side="BUY",
            qty=1,
            limit_price=24000.0,
            reason="index hybrid",
            action_kind="entry",
            resulting_position="long",
            signal_action="BUY_CE_BIAS",
        )

    adm = EntryCycleAdmission(max_open_symbols=10)
    for sym in ("NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"):
        await adm.stage(
            PendingEntry(
                symbol=sym,
                hybrid_score=0.95,
                confidence=0.9,
                intents=[_intent(sym)],
                safe_mode="dry_run",
                limits={"max_open_symbols": 10},
            )
        )
    await adm.stage(
        PendingEntry(
            symbol="RELIANCE",
            hybrid_score=0.80,
            confidence=0.7,
            intents=[_intent("RELIANCE")],
            safe_mode="dry_run",
            limits={"max_open_symbols": 10},
        )
    )
    cands = {c.symbol for c in adm.pending_candidates()}
    assert cands == {"RELIANCE"}
    assert "NIFTY" not in cands
