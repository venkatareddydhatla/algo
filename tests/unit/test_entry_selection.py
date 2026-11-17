"""Unit tests for |hybrid|-ranked equity entry selection (anti-churn + dynamic)."""

from __future__ import annotations

import pytest

from options_portal_domain.entry_selection import (
    EntryCandidate,
    dynamic_target_open,
    entry_selection_config,
    free_open_slots,
    rank_entry_candidates,
    select_entries_for_empty_slots,
)


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
    assert dynamic_target_open(open_count=0, qualifying_new=20, floor=4, ceiling=15) == 15
    assert dynamic_target_open(open_count=2, qualifying_new=3, floor=4, ceiling=15) == 5
    # Below floor when not enough qualify — do not invent trades
    assert dynamic_target_open(open_count=0, qualifying_new=2, floor=4, ceiling=15) == 2
    assert dynamic_target_open(open_count=8, qualifying_new=10, floor=4, ceiling=15) == 15


def test_dynamic_admits_only_above_threshold_strongest_first():
    candidates = [
        EntryCandidate("WEAK", 0.46),  # below 0.55
        EntryCandidate("STRONG", -0.92),
        EntryCandidate("MID", 0.70),
        EntryCandidate("EDGE", 0.55),
        EntryCandidate("LOW", 0.54),
    ]
    picked = select_entries_for_empty_slots(
        candidates,
        open_symbols=[],
        max_open_symbols=15,
        dynamic=True,
        floor=4,
        ceiling=15,
        min_hybrid_abs=0.55,
    )
    assert [c.symbol for c in picked] == ["STRONG", "MID", "EDGE"]
    # 3 qualify < floor 4 → admit all 3 (no weak fillers)


def test_dynamic_fills_toward_ceiling_with_opens():
    opens = ["KEEP1", "KEEP2"]
    candidates = [
        EntryCandidate(f"S{i}", 0.60 + i * 0.01) for i in range(20)
    ]
    picked = select_entries_for_empty_slots(
        candidates,
        open_symbols=opens,
        max_open_symbols=15,
        dynamic=True,
        floor=4,
        ceiling=15,
        min_hybrid_abs=0.55,
    )
    # target = clamp(2+20, 4, 15) = 15 → 13 free slots; strongest first
    assert len(picked) == 13
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
        max_open_symbols=15,
        dynamic=True,
        floor=4,
        ceiling=15,
        min_hybrid_abs=0.55,
    )
    # target = clamp(4+1, 4, 15) = 5 → 1 free → STRONG only
    assert [c.symbol for c in picked] == ["STRONG"]


def test_entry_selection_config_defaults():
    cfg = {"trading": {"risk": {"max_open_symbols": 10}}}
    sel = entry_selection_config(cfg)
    assert sel["enabled"] is True
    assert sel["rank_by"] == "abs_hybrid"
    assert sel["anti_churn"] == "empty_slots_only"
    assert sel["max_open_symbols"] == 10
    assert sel["dynamic_capacity"]["enabled"] is False


def test_entry_selection_config_dynamic_knobs():
    cfg = {
        "trading": {
            "risk": {
                "max_open_symbols": 15,
                "entry_selection": {
                    "enabled": True,
                    "rank_by": "abs_hybrid",
                    "anti_churn": "empty_slots_only",
                    "dynamic_capacity": {
                        "enabled": True,
                        "floor": 4,
                        "ceiling": 15,
                        "min_hybrid_abs": 0.55,
                    },
                },
            }
        }
    }
    sel = entry_selection_config(cfg)
    dyn = sel["dynamic_capacity"]
    assert dyn["enabled"] is True
    assert dyn["floor"] == 4
    assert dyn["ceiling"] == 15
    assert dyn["min_hybrid_abs"] == 0.55
    # ceiling cannot exceed hard cap
    cfg["trading"]["risk"]["entry_selection"]["dynamic_capacity"]["ceiling"] = 99
    sel2 = entry_selection_config(cfg)
    assert sel2["dynamic_capacity"]["ceiling"] == 15


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


def _load_worker_entry_admission():
    """Load worker entry_admission without clashing with apps/api ``app``."""
    import importlib.util
    import sys
    from pathlib import Path

    name = "options_portal_worker_entry_admission"
    if name in sys.modules:
        return sys.modules[name]
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
                    "max_open_symbols": 15,
                    "entry_selection": {
                        "dynamic_capacity": {
                            "enabled": True,
                            "floor": 4,
                            "ceiling": 15,
                            "min_hybrid_abs": 0.55,
                        }
                    },
                }
            }
        }
    )
    assert adm is not None
    assert adm.max_open_symbols == 15
    assert adm.anti_churn == "empty_slots_only"
    assert adm.dynamic_enabled is True
    assert adm.floor == 4
    assert adm.ceiling == 15
    assert adm.min_hybrid_abs == 0.55


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
