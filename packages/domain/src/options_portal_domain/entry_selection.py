"""Equity dry-run entry selection: rank by |hybrid|, dynamic capacity, anti-churn.

Policy (v1)
-----------
- Rank **new** entry candidates by ``|hybrid_score|`` (strongest first).
- **Dynamic capacity** (optional): target open-book size scales with how many
  fresh candidates clear ``min_hybrid_abs``, clamped to ``[floor, ceiling]``.
  Admit only candidates above that |hybrid| threshold.
- Fill only **empty** slots under the effective target — never rotate an
  existing open for a tiny / modest score edge (brokerage / churn control).
- Soft flatten and overnight ``|hybrid| >= 0.70`` stay in session_gates;
  this module does not change exit / overnight rules.
- ``max_open_symbols`` / dynamic ``ceiling`` is the hard risk cap.

Anti-churn choice: ``empty_slots_only`` (not margin-based replacement).
Documented so ops can revisit later without guessing scan-order behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Collection, Iterable, Literal, Sequence

AntiChurnMode = Literal["empty_slots_only"]
RankBy = Literal["abs_hybrid"]

# Sensible defaults when YAML omits knobs (ops can manage the list in config).
DEFAULT_CEILING = 15
DEFAULT_FLOOR = 4
DEFAULT_MIN_HYBRID_ABS = 0.55


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


def _dedupe_fresh(
    candidates: Sequence[EntryCandidate] | Iterable[EntryCandidate],
    open_set: set[str],
) -> list[EntryCandidate]:
    """Drop already-open symbols; keep strongest |hybrid| per symbol."""
    fresh = [
        c
        for c in candidates
        if str(c.symbol).strip().upper() and str(c.symbol).strip().upper() not in open_set
    ]
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
    return list(best.values())


def dynamic_target_open(
    *,
    open_count: int,
    qualifying_new: int,
    floor: int,
    ceiling: int,
) -> int:
    """Effective book size for this cycle: clamp(opens + qualify, floor, ceiling).

    If fewer than ``floor`` names qualify in total, the target may sit below
    the floor (we never invent weak trades). Existing opens are never closed
    here — anti-churn stays ``empty_slots_only``.
    """
    floor_n = max(0, int(floor))
    ceiling_n = max(floor_n, int(ceiling))
    open_n = max(0, int(open_count))
    qual_n = max(0, int(qualifying_new))
    raw = open_n + qual_n
    if raw < floor_n:
        # Not enough strength to justify the floor — admit what qualifies only.
        return min(raw, ceiling_n)
    return max(floor_n, min(ceiling_n, raw))


def select_entries_for_empty_slots(
    candidates: Sequence[EntryCandidate] | Iterable[EntryCandidate],
    *,
    open_symbols: Collection[str],
    max_open_symbols: int,
    min_hybrid_abs: float | None = None,
    floor: int | None = None,
    ceiling: int | None = None,
    dynamic: bool = False,
) -> list[EntryCandidate]:
    """Admit strongest |hybrid| candidates into free slots only (anti-churn).

    Existing opens are never displaced here. Symbols already open are ignored
    even if they appear in ``candidates``.

    When ``dynamic`` is True:
    - drop candidates with ``|hybrid| < min_hybrid_abs``;
    - target book = ``dynamic_target_open(...)`` using floor/ceiling;
    - hard cap remains ``max(ceiling, max_open_symbols)`` (risk gate).
    """
    open_set = {str(s).strip().upper() for s in open_symbols if str(s).strip()}
    hard_cap = max(0, int(max_open_symbols))
    fresh = _dedupe_fresh(candidates, open_set)

    if dynamic:
        thr = float(min_hybrid_abs if min_hybrid_abs is not None else DEFAULT_MIN_HYBRID_ABS)
        floor_n = int(floor if floor is not None else DEFAULT_FLOOR)
        ceiling_n = int(ceiling if ceiling is not None else (hard_cap or DEFAULT_CEILING))
        # Never exceed the risk hard cap when both are set.
        if hard_cap > 0:
            ceiling_n = min(max(0, ceiling_n), hard_cap)
        else:
            ceiling_n = max(0, ceiling_n)
        qualifying = [c for c in fresh if c.hybrid_abs >= thr]
        target = dynamic_target_open(
            open_count=len(open_set),
            qualifying_new=len(qualifying),
            floor=floor_n,
            ceiling=ceiling_n,
        )
        slots = max(0, target - len(open_set))
        ranked = rank_entry_candidates(qualifying)
        return ranked[:slots]

    slots = free_open_slots(open_set, hard_cap)
    if slots <= 0:
        return []
    ranked = rank_entry_candidates(fresh)
    return ranked[:slots]


def entry_selection_config(cfg: dict[str, Any] | None) -> dict[str, Any]:
    """Read ``trading.risk`` + optional ``entry_selection`` / ``dynamic_capacity``."""
    root = cfg or {}
    risk = dict((root.get("trading") or {}).get("risk") or {})
    sel = dict(risk.get("entry_selection") or {})
    anti = str(sel.get("anti_churn", "empty_slots_only")).strip().lower()
    if anti not in {"empty_slots_only"}:
        anti = "empty_slots_only"
    rank_by = str(sel.get("rank_by", "abs_hybrid")).strip().lower()
    if rank_by not in {"abs_hybrid"}:
        rank_by = "abs_hybrid"

    hard_cap = int(risk.get("max_open_symbols", DEFAULT_CEILING))
    dyn_raw = sel.get("dynamic_capacity")
    dyn: dict[str, Any] = dict(dyn_raw) if isinstance(dyn_raw, dict) else {}
    # Enable when the block is present (default on) unless explicitly false.
    if dyn_raw is None:
        dynamic_enabled = False
    else:
        dynamic_enabled = bool(dyn.get("enabled", True))

    ceiling_n = int(dyn.get("ceiling", hard_cap if hard_cap > 0 else DEFAULT_CEILING))
    if hard_cap > 0:
        ceiling_n = min(max(0, ceiling_n), hard_cap)
    else:
        ceiling_n = max(0, ceiling_n)
    floor_n = int(dyn.get("floor", DEFAULT_FLOOR))
    floor_n = max(0, min(floor_n, ceiling_n if ceiling_n > 0 else floor_n))
    min_h = float(dyn.get("min_hybrid_abs", DEFAULT_MIN_HYBRID_ABS))

    return {
        "enabled": bool(sel.get("enabled", True)),
        "rank_by": rank_by,
        "anti_churn": anti,
        "max_open_symbols": hard_cap,
        "dynamic_capacity": {
            "enabled": dynamic_enabled,
            "floor": floor_n,
            "ceiling": ceiling_n if ceiling_n > 0 else hard_cap,
            "min_hybrid_abs": min_h,
        },
    }
