"""Equity dry-run entry selection: rank by |hybrid|, dynamic capacity, anti-churn.

Policy (v1)
-----------
- Rank **new** entry candidates by ``|hybrid_score|`` (strongest first).
- **Dynamic capacity** (optional): target open-book size scales with how many
  fresh candidates clear ``min_hybrid_abs``, clamped to ``[floor, ceiling]``.
  Admit only candidates above that |hybrid| threshold.
- Fill only **empty** slots under the effective target — never rotate an
  existing open for a tiny / modest score edge (brokerage / churn control).
- **Re-entry cooloff:** after closing a symbol (any reason), block new entry
  on the same symbol for ``reentry_cooloff.minutes`` within the same IST
  NSE session day.
- **Afternoon floor (optional):** after ``afternoon_after_ist``, raise the
  admit threshold to ``afternoon_min_hybrid_abs`` (stricter midday/afternoon).
- Soft flatten and overnight ``|hybrid| >= 0.70`` stay in session_gates;
  this module does not change exit / overnight rules.
- ``max_open_symbols`` / dynamic ``ceiling`` is the hard risk cap.

Anti-churn choice: ``empty_slots_only`` (not margin-based replacement).
Documented so ops can revisit later without guessing scan-order behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Collection, Iterable, Literal, Mapping, Sequence

from options_portal_domain.calendar import pnl_ist_day
from options_portal_domain.tz import ist

AntiChurnMode = Literal["empty_slots_only"]
RankBy = Literal["abs_hybrid"]

# Sensible defaults when YAML omits knobs (ops can manage the list in config).
# 2026-11-18 Day3: far fewer / higher conviction (was ceiling 8 / 0.65 / 90m).
DEFAULT_CEILING = 5
DEFAULT_FLOOR = 3
DEFAULT_MIN_HYBRID_ABS = 0.72
DEFAULT_AFTERNOON_MIN_HYBRID_ABS = 0.75
DEFAULT_AFTERNOON_AFTER_IST = "12:00"
DEFAULT_REENTRY_COOLOFF_MINUTES = 180


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


def _as_ist(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        # Naive → treat as already-IST wall clock (tests + local stamps).
        return dt.replace(tzinfo=ist())
    return dt.astimezone(ist())


def parse_hhmm(value: str) -> tuple[int, int]:
    """Parse ``HH:MM`` → (hour, minute)."""
    hour, minute = str(value).strip().split(":")[:2]
    return int(hour), int(minute)


def at_or_after_hhmm(hhmm: str, now: datetime) -> bool:
    """True when IST clock HH:MM is at or after ``hhmm``."""
    clock = _as_ist(now)
    ch, cm = clock.hour, clock.minute
    th, tm = parse_hhmm(hhmm)
    return (ch, cm) >= (th, tm)


def effective_min_hybrid_abs(
    base_min: float,
    *,
    now: datetime,
    afternoon_after_ist: str | None = None,
    afternoon_min_hybrid_abs: float | None = None,
) -> float:
    """Raise |hybrid| floor after afternoon cutover when configured.

    Soft flatten / overnight hold threshold (0.70) is separate — this only
    affects **new entry admission**.
    """
    base = float(base_min)
    if afternoon_after_ist is None or afternoon_min_hybrid_abs is None:
        return base
    aft = float(afternoon_min_hybrid_abs)
    if aft <= base:
        return base
    if at_or_after_hhmm(str(afternoon_after_ist), now):
        return aft
    return base


def in_reentry_cooloff(
    *,
    last_close: datetime | None,
    now: datetime,
    cooloff_minutes: int,
    same_session_only: bool = True,
    session_day: date | None = None,
) -> bool:
    """True when a symbol was closed recently enough to block re-entry.

    ``same_session_only`` (default): cooloff only applies when the close falls
    on the same NSE session IST day as ``now`` (via ``pnl_ist_day``). A close
    from a prior session does not block the next morning.
    """
    if last_close is None or int(cooloff_minutes) <= 0:
        return False
    close_ist = _as_ist(last_close)
    now_ist = _as_ist(now)
    if same_session_only:
        close_day = pnl_ist_day(close_ist)
        now_day = session_day if session_day is not None else pnl_ist_day(now_ist)
        if close_day != now_day:
            return False
    return (now_ist - close_ist) < timedelta(minutes=int(cooloff_minutes))


def symbols_in_reentry_cooloff(
    last_close_by_symbol: Mapping[str, datetime],
    *,
    now: datetime,
    cooloff_minutes: int,
    same_session_only: bool = True,
    session_day: date | None = None,
) -> set[str]:
    """Symbols blocked from new entry by re-entry cooloff."""
    blocked: set[str] = set()
    for raw_sym, closed_at in last_close_by_symbol.items():
        sym = str(raw_sym).strip().upper()
        if not sym:
            continue
        if in_reentry_cooloff(
            last_close=closed_at,
            now=now,
            cooloff_minutes=cooloff_minutes,
            same_session_only=same_session_only,
            session_day=session_day,
        ):
            blocked.add(sym)
    return blocked


def last_exit_times_from_fills(
    fills: Sequence[Mapping[str, Any]] | Iterable[Mapping[str, Any]],
) -> dict[str, datetime]:
    """Map symbol → latest exit/flip_close timestamp (IST-aware).

    Uses ``ts_ist`` / ``ts`` / ``ts_ist_display`` when present. Ignores entry
    fills. Keeps the newest close per symbol.
    """
    _EXIT = frozenset({"exit", "flip_close"})
    out: dict[str, datetime] = {}
    for fill in fills:
        kind = str(fill.get("action_kind") or "").strip().lower()
        # Legacy rows: realized_pnl set on closes
        if kind:
            if kind not in _EXIT:
                continue
        elif fill.get("realized_pnl") is None:
            continue
        sym = str(fill.get("symbol") or "").strip().upper()
        if not sym:
            continue
        raw = (
            fill.get("ts_ist")
            or fill.get("ts")
            or fill.get("ts_utc")
            or fill.get("ts_ist_display")
        )
        parsed = _parse_fill_ts(raw)
        if parsed is None:
            continue
        prev = out.get(sym)
        if prev is None or parsed > prev:
            out[sym] = parsed
    return out


def _parse_fill_ts(raw: Any) -> datetime | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text == "—":
        return None
    if text.endswith(" IST"):
        text = text[: -len(" IST")].strip()
        try:
            # YYYY-MM-DD HH:MM
            return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=ist())
        except ValueError:
            try:
                return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ist())
            except ValueError:
                return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_ist(dt)


def select_entries_for_empty_slots(
    candidates: Sequence[EntryCandidate] | Iterable[EntryCandidate],
    *,
    open_symbols: Collection[str],
    max_open_symbols: int,
    min_hybrid_abs: float | None = None,
    floor: int | None = None,
    ceiling: int | None = None,
    dynamic: bool = False,
    cooloff_symbols: Collection[str] | None = None,
) -> list[EntryCandidate]:
    """Admit strongest |hybrid| candidates into free slots only (anti-churn).

    Existing opens are never displaced here. Symbols already open are ignored
    even if they appear in ``candidates``.

    ``cooloff_symbols`` are treated like already-open for admission (blocked
    re-entry) but do not consume open-book slots.

    When ``dynamic`` is True:
    - drop candidates with ``|hybrid| < min_hybrid_abs``;
    - target book = ``dynamic_target_open(...)`` using floor/ceiling;
    - hard cap remains ``max(ceiling, max_open_symbols)`` (risk gate).
    """
    open_set = {str(s).strip().upper() for s in open_symbols if str(s).strip()}
    cooloff_set = {
        str(s).strip().upper() for s in (cooloff_symbols or ()) if str(s).strip()
    }
    hard_cap = max(0, int(max_open_symbols))
    # Cooloff symbols cannot enter; still not counted as open for capacity.
    blocked = open_set | cooloff_set
    fresh = _dedupe_fresh(candidates, blocked)

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

    aft_raw = dyn.get("afternoon_after_ist", DEFAULT_AFTERNOON_AFTER_IST)
    aft_hhmm = str(aft_raw).strip() if aft_raw is not None else ""
    aft_min_raw = dyn.get("afternoon_min_hybrid_abs", DEFAULT_AFTERNOON_MIN_HYBRID_ABS)
    # Explicit null / empty disables afternoon raise; otherwise use defaults.
    if aft_hhmm and aft_min_raw is not None:
        afternoon_after_ist: str | None = aft_hhmm
        afternoon_min: float | None = float(aft_min_raw)
    else:
        afternoon_after_ist = None
        afternoon_min = None

    cool_raw = sel.get("reentry_cooloff")
    cool: dict[str, Any] = dict(cool_raw) if isinstance(cool_raw, dict) else {}
    if cool_raw is None:
        # Default on when omitted from YAML — brokerage churn control.
        cooloff_enabled = True
        cooloff_minutes = DEFAULT_REENTRY_COOLOFF_MINUTES
        cooloff_same_session = True
    else:
        cooloff_enabled = bool(cool.get("enabled", True))
        cooloff_minutes = int(cool.get("minutes", DEFAULT_REENTRY_COOLOFF_MINUTES))
        cooloff_same_session = bool(cool.get("same_session_only", True))

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
            "afternoon_after_ist": afternoon_after_ist,
            "afternoon_min_hybrid_abs": afternoon_min,
        },
        "reentry_cooloff": {
            "enabled": cooloff_enabled,
            "minutes": max(0, cooloff_minutes),
            "same_session_only": cooloff_same_session,
        },
    }
