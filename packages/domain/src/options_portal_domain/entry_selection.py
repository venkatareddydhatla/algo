"""Equity dry-run entry selection: rank by |hybrid|, anti-churn vs opens.

Policy (v1)
-----------
- Rank **new** entry candidates by ``|hybrid_score|`` (strongest first).
- Fill only **empty** ``max_open_symbols`` slots — never rotate an existing
  open for a tiny / modest score edge (brokerage / churn control).
- Soft flatten and overnight ``|hybrid| >= 0.70`` stay in session_gates;
  this module does not change exit / overnight rules.
- ``max_open_symbols`` remains a hard risk cap (default 10).

Anti-churn choice: ``empty_slots_only`` (not margin-based replacement).
Documented so ops can revisit later without guessing scan-order behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Collection, Iterable, Literal, Sequence

AntiChurnMode = Literal["empty_slots_only"]
RankBy = Literal["abs_hybrid"]


@dataclass(frozen=True)
class EntryCandidate:
    """A symbol that wants a new equity entry this scan cycle."""

    symbol: str
    hybrid_score: float
    confidence: float = 0.0

    @property
    def hybrid_abs(self) -> float:
        return abs(float(self.hybrid_score))


def free_open_slots(
    open_symbols: Collection[str],
    max_open_symbols: int,
) -> int:
    """How many distinct new symbols may enter under the risk cap."""
    max_open = max(0, int(max_open_symbols))
    open_n = len({str(s).strip().upper() for s in open_symbols if str(s).strip()})
    return max(0, max_open - open_n)


def rank_entry_candidates(
    candidates: Sequence[EntryCandidate] | Iterable[EntryCandidate],
) -> list[EntryCandidate]:
    """Strongest |hybrid| first; ties → higher confidence, then symbol asc."""
    rows = list(candidates)
    return sorted(
        rows,
        key=lambda c: (
            -c.hybrid_abs,
            -float(c.confidence or 0.0),
            str(c.symbol).upper(),
        ),
    )


def select_entries_for_empty_slots(
    candidates: Sequence[EntryCandidate] | Iterable[EntryCandidate],
    *,
    open_symbols: Collection[str],
    max_open_symbols: int,
) -> list[EntryCandidate]:
    """Admit strongest |hybrid| candidates into free slots only (anti-churn).

    Existing opens are never displaced here. Symbols already open are ignored
    even if they appear in ``candidates``.
    """
    open_set = {str(s).strip().upper() for s in open_symbols if str(s).strip()}
    slots = free_open_slots(open_set, max_open_symbols)
    if slots <= 0:
        return []

    fresh = [
        c
        for c in candidates
        if str(c.symbol).strip().upper() and str(c.symbol).strip().upper() not in open_set
    ]
    # De-dupe by symbol keeping strongest |hybrid|
    best: dict[str, EntryCandidate] = {}
    for c in fresh:
        sym = str(c.symbol).strip().upper()
        prev = best.get(sym)
        if prev is None or (c.hybrid_abs, c.confidence) > (prev.hybrid_abs, prev.confidence):
            best[sym] = EntryCandidate(
                symbol=sym,
                hybrid_score=float(c.hybrid_score),
                confidence=float(c.confidence or 0.0),
            )
    ranked = rank_entry_candidates(best.values())
    return ranked[:slots]


def entry_selection_config(cfg: dict[str, Any] | None) -> dict[str, Any]:
    """Read ``trading.risk`` + optional ``entry_selection`` block."""
    root = cfg or {}
    risk = dict((root.get("trading") or {}).get("risk") or {})
    sel = dict(risk.get("entry_selection") or {})
    anti = str(sel.get("anti_churn", "empty_slots_only")).strip().lower()
    if anti not in {"empty_slots_only"}:
        anti = "empty_slots_only"
    rank_by = str(sel.get("rank_by", "abs_hybrid")).strip().lower()
    if rank_by not in {"abs_hybrid"}:
        rank_by = "abs_hybrid"
    return {
        "enabled": bool(sel.get("enabled", True)),
        "rank_by": rank_by,
        "anti_churn": anti,
        "max_open_symbols": int(risk.get("max_open_symbols", 10)),
    }
