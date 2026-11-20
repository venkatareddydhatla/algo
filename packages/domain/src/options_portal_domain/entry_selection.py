"""Equity dry-run entry selection: rank by |hybrid|, dynamic capacity, anti-churn.

Policy (dual-session high conviction)
-------------------------------------
- Rank **new** entry candidates by ``|hybrid_score|`` (strongest first).
- **Morning session** [open, morning_until_ist) (default **09:15–10:00 IST**):
  admit only ``|hybrid| ≥ morning_min_hybrid_abs`` (default **0.75**);
  **max_new_entries** total for the morning window (default **3**) — not a
  perpetual refill when names exit.
- **Rest of day** until ``no_new_entries_after`` (default **11:30 IST**):
  admit ``|hybrid| ≥ min_hybrid_abs`` (default **0.72**); empty_slots_only;
  cooloff **180m**.
- **Dynamic capacity**: target open-book size scales with how many fresh
  candidates clear the effective |hybrid| floor, clamped to ``[floor, ceiling]``
  (default floor **3**, ceiling **12**). Conviction floors + entry cutoffs +
  cooloff + higher exit thresholds do the work — do **not** use a tiny hard
  open-cap as the main filter.
- Fill only **empty** slots under the effective target — never rotate an
  existing open for a tiny / modest score edge (brokerage / churn control).
- **Re-entry cooloff:** after closing a symbol (any reason), block new entry
  on the same symbol for ``reentry_cooloff.minutes`` within the same IST
  NSE session day.
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

# Dual-session defaults: conviction floors + entry cutoffs do the work;
# open-book ceiling restored so best symbols are not blocked (was Day3 hard 5).
DEFAULT_CEILING = 12
DEFAULT_FLOOR = 3
DEFAULT_MIN_HYBRID_ABS = 0.72
DEFAULT_MORNING_UNTIL_IST = "10:00"
DEFAULT_MORNING_MIN_HYBRID_ABS = 0.75
DEFAULT_MORNING_MAX_NEW_ENTRIES = 3
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


def before_hhmm(hhmm: str, now: datetime) -> bool:
    """True when IST clock HH:MM is strictly before ``hhmm``."""
    return not at_or_after_hhmm(hhmm, now)


def in_morning_entry_window(
    now: datetime,
    *,
    open_ist: str = "09:15",
    morning_until_ist: str = DEFAULT_MORNING_UNTIL_IST,
) -> bool:
    """True during morning entry session ``[open_ist, morning_until_ist)`` IST."""
    return at_or_after_hhmm(open_ist, now) and before_hhmm(morning_until_ist, now)


def effective_min_hybrid_abs(
    base_min: float,
    *,
    now: datetime,
    morning_until_ist: str | None = None,
    morning_min_hybrid_abs: float | None = None,
    open_ist: str = "09:15",
    afternoon_after_ist: str | None = None,
    afternoon_min_hybrid_abs: float | None = None,
) -> float:
    """Session-aware |hybrid| floor for **new entry admission**.

    Morning (before ``morning_until_ist``): raise to ``morning_min_hybrid_abs``
    when configured higher than base. Afternoon raise still available as a
    belt (usually moot when entries cut at 11:30). Soft flatten / overnight
    hold threshold (0.70) is separate.
    """
    base = float(base_min)
    if (
        morning_until_ist is not None
        and morning_min_hybrid_abs is not None
        and in_morning_entry_window(
            now, open_ist=open_ist, morning_until_ist=str(morning_until_ist)
        )
    ):
        morn = float(morning_min_hybrid_abs)
        if morn > base:
            return morn
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


def count_morning_entries_from_fills(
    fills: Sequence[Mapping[str, Any]] | Iterable[Mapping[str, Any]],
    *,
    now: datetime,
    open_ist: str = "09:15",
    morning_until_ist: str = DEFAULT_MORNING_UNTIL_IST,
    session_day: date | None = None,
) -> int:
    """Count new entry / flip_open fills in today's morning window.

    Used for the morning **max_new_entries** budget (not perpetual refill).
    Prior-session fills and rest-of-day entries do not count.
    """
    _ENTRY = frozenset({"entry", "flip_open"})
    now_ist = _as_ist(now)
    day = session_day if session_day is not None else pnl_ist_day(now_ist)
    open_hm = parse_hhmm(open_ist)
    until_hm = parse_hhmm(morning_until_ist)
    count = 0
    for fill in fills:
        kind = str(fill.get("action_kind") or "").strip().lower()
        if kind not in _ENTRY:
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
        if pnl_ist_day(parsed) != day:
            continue
        hm = (parsed.hour, parsed.minute)
        if open_hm <= hm < until_hm:
            count += 1
    return count


def morning_entry_slots_remaining(
    *,
    morning_entries_used: int,
    max_new_entries: int,
) -> int:
    """How many morning-window new entries may still be admitted today."""
    budget = max(0, int(max_new_entries))
    used = max(0, int(morning_entries_used))
    return max(0, budget - used)


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
    max_new_entries: int | None = None,
) -> list[EntryCandidate]:
    """Admit strongest |hybrid| candidates into free slots only (anti-churn).

    Existing opens are never displaced here. Symbols already open are ignored
    even if they appear in ``candidates``.

    ``cooloff_symbols`` are treated like already-open for admission (blocked
    re-entry) but do not consume open-book slots.

    ``max_new_entries`` (optional): hard cap on how many may be admitted this
    call — used for morning budget remaining (not perpetual refill).

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
    budget_cap = (
        max(0, int(max_new_entries)) if max_new_entries is not None else None
    )

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
        if budget_cap is not None:
            slots = min(slots, budget_cap)
        ranked = rank_entry_candidates(qualifying)
        return ranked[:slots]

    slots = free_open_slots(open_set, hard_cap)
    if budget_cap is not None:
        slots = min(slots, budget_cap)
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

    # Morning session: higher conviction + hard new-entry budget (not refill).
    morn_raw = sel.get("morning_session")
    if morn_raw is None and isinstance(dyn.get("morning_session"), dict):
        morn_raw = dyn.get("morning_session")
    morn: dict[str, Any] = dict(morn_raw) if isinstance(morn_raw, dict) else {}
    if morn_raw is None:
        # Defaults on when omitted — dual-session model.
        morning_enabled = True
        morning_until = DEFAULT_MORNING_UNTIL_IST
        morning_min = DEFAULT_MORNING_MIN_HYBRID_ABS
        morning_max_new = DEFAULT_MORNING_MAX_NEW_ENTRIES
    else:
        morning_enabled = bool(morn.get("enabled", True))
        morning_until = str(
            morn.get("until_ist", DEFAULT_MORNING_UNTIL_IST)
        ).strip() or DEFAULT_MORNING_UNTIL_IST
        morning_min = float(
            morn.get("min_hybrid_abs", DEFAULT_MORNING_MIN_HYBRID_ABS)
        )
        morning_max_new = int(
            morn.get("max_new_entries", DEFAULT_MORNING_MAX_NEW_ENTRIES)
        )

    aft_raw = dyn.get("afternoon_after_ist")
    # Dual-session tip: afternoon raise optional; omit / null disables.
    # Keep explicit YAML values when set (belt if entry window extended).
    if "afternoon_after_ist" not in dyn and "afternoon_min_hybrid_abs" not in dyn:
        # No afternoon keys → disabled (entries cut at 11:30; morning owns raise).
        afternoon_after_ist: str | None = None
        afternoon_min: float | None = None
    else:
        aft_hhmm = str(aft_raw).strip() if aft_raw is not None else ""
        aft_min_raw = dyn.get("afternoon_min_hybrid_abs", DEFAULT_AFTERNOON_MIN_HYBRID_ABS)
        if aft_hhmm and aft_min_raw is not None:
            afternoon_after_ist = aft_hhmm
            afternoon_min = float(aft_min_raw)
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
        "morning_session": {
            "enabled": morning_enabled,
            "until_ist": morning_until,
            "min_hybrid_abs": morning_min,
            "max_new_entries": max(0, morning_max_new),
        },
        "reentry_cooloff": {
            "enabled": cooloff_enabled,
            "minutes": max(0, cooloff_minutes),
            "same_session_only": cooloff_same_session,
        },
    }
