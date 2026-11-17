"""Unit tests for |hybrid|-ranked equity entry selection (anti-churn)."""

from __future__ import annotations

import pytest

from options_portal_domain.entry_selection import (
    EntryCandidate,
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


def test_entry_selection_config_defaults():
    cfg = {"trading": {"risk": {"max_open_symbols": 10}}}
    sel = entry_selection_config(cfg)
    assert sel["enabled"] is True
    assert sel["rank_by"] == "abs_hybrid"
    assert sel["anti_churn"] == "empty_slots_only"
    assert sel["max_open_symbols"] == 10


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


def test_admission_from_config_none_when_disabled():
    from app.entry_admission import EntryCycleAdmission

    assert (
        EntryCycleAdmission.from_config(
            {"trading": {"risk": {"entry_selection": {"enabled": False}}}}
        )
        is None
    )
    adm = EntryCycleAdmission.from_config(
        {"trading": {"risk": {"max_open_symbols": 10}}}
    )
    assert adm is not None
    assert adm.max_open_symbols == 10
    assert adm.anti_churn == "empty_slots_only"


@pytest.mark.asyncio
async def test_admission_stages_strongest_and_selects():
    from app.entry_admission import EntryCycleAdmission, PendingEntry
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
