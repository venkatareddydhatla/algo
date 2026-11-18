"""High-conviction multi-factor equity F&O stock entry gate (indices excluded).

Venkata rule card (supersedes looser dual-session entry thresholds):

**Universe:** NSE F&O **stocks** only; no index trades.
**Entry window:** 09:15–15:20 IST (``no_new_entries_after=15:20``).

**Entry — ALL must pass**
1. ``|hybrid|`` floors (IST): ≥0.75 before 12:00; ≥0.80 after 12:00;
   ≥0.85 after 14:20. Adaptive ±0.05 from baseline (week WR), clamp [0.70, 0.90].
2. Multi-timeframe: 15m and 1h same direction (bullish/bearish).
3. Relative volume: last 15m vol ≥ 1.5× mean of prior 20 bars.
4. ATR(14) > 1.2% of price.
5. Proximity block: no entry within 0.2×ATR of major daily high/low.
6. News/sentiment: block if last 1h news sentiment conflicts with signal.
7. Same-symbol cooloff **120 minutes** per session after **any** trade.
8. Max open **6**; if rolling WR < 50% → max open **4**.
   WR window: **last 20 closed equity round-trips** (documented).
9. Rank by ``|hybrid|`` strongest-first; ``empty_slots_only`` anti-churn.

**Exit / soft flatten**
- ATR stop + trail; discretionary profit requires min RRR ≥ 1.2.
- Soft flatten from **15:28 IST** unless ``|hybrid| ≥ 0.85`` and trend confirmed.
- Risk exits remain cash-session only (existing after-hours gate).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Literal, Mapping, Sequence

from options_portal_domain.calendar import pnl_ist_day
from options_portal_domain.candles_agg import (
    Direction,
    aggregate_15m_to_1h,
    bar_direction,
    direction_matches_signal,
    session_daily_high_low,
)
from options_portal_domain.entry_selection import at_or_after_hhmm
from options_portal_domain.round_trips import pair_round_trip_trades
from options_portal_domain.tz import ist
from options_portal_domain.types import Candle, NewsItem, SentimentResult

# --- YAML defaults (trading.risk.high_conviction / session) -----------------
DEFAULT_ENTRY_OPEN_IST = "09:15"
DEFAULT_NO_NEW_ENTRIES_AFTER = "15:20"
DEFAULT_SOFT_FLATTEN_IST = "15:28"
DEFAULT_SOFT_FLATTEN_UNTIL_IST = "15:40"
DEFAULT_OVERNIGHT_HOLD_MIN_HYBRID = 0.85

DEFAULT_MIN_HYBRID_BEFORE_12 = 0.75
DEFAULT_MIN_HYBRID_AFTER_12 = 0.80
DEFAULT_MIN_HYBRID_AFTER_1420 = 0.85
DEFAULT_AFTERNOON_AFTER_IST = "12:00"
DEFAULT_LATE_ENTRY_AFTER_IST = "14:20"

DEFAULT_RVOL_MULT = 1.5
DEFAULT_RVOL_LOOKBACK = 20
DEFAULT_ATR_PERIOD = 14
DEFAULT_ATR_MIN_PCT_OF_PRICE = 1.2
DEFAULT_PROXIMITY_ATR_MULT = 0.2
DEFAULT_NEWS_CONFLICT_LOOKBACK_MIN = 60
DEFAULT_COOLOFF_MINUTES = 120

DEFAULT_MAX_OPEN = 6
DEFAULT_MAX_OPEN_LOW_WR = 4
DEFAULT_WR_THRESHOLD = 0.50
# Rolling WR for max-open throttle: last N closed equity round-trips.
DEFAULT_WR_WINDOW_TRIPS = 20

DEFAULT_ADAPTIVE_WEEK_WR_THRESHOLD = 0.55
DEFAULT_ADAPTIVE_STEP = 0.05
DEFAULT_ADAPTIVE_CLAMP_LO = 0.70
DEFAULT_ADAPTIVE_CLAMP_HI = 0.90
DEFAULT_ADAPTIVE_LOOKBACK_TRADING_DAYS = 5

DEFAULT_MIN_RRR = 1.2

KV_KEY_ADAPTIVE_DELTA = "high_conviction.adaptive_min_hybrid_delta"
KV_KEY_ADAPTIVE_BASELINE = "high_conviction.adaptive_baseline_min_hybrid"


FactorName = Literal[
    "hybrid_floor",
    "mtf",
    "rvol",
    "atr_pct",
    "proximity",
    "news_1h",
    "cooloff",
    "max_open",
    "index_block",
    "entry_window",
]


@dataclass(frozen=True)
class FactorResult:
    """One pass/fail factor with a short reason for skip/entry logs."""

    name: FactorName | str
    passed: bool
    detail: str


@dataclass(frozen=True)
class GateResult:
    """Aggregate multi-factor gate outcome for one symbol."""

    admitted: bool
    factors: tuple[FactorResult, ...]
    effective_min_hybrid: float
    hybrid_abs: float
    reason: str = ""

    @property
    def failing(self) -> list[FactorResult]:
        return [f for f in self.factors if not f.passed]

    def log_line(self, symbol: str, *, ts: datetime | None = None) -> str:
        clock = ts or datetime.now(tz=ist())
        if clock.tzinfo is None:
            clock = clock.replace(tzinfo=ist())
        else:
            clock = clock.astimezone(ist())
        stamp = clock.strftime("%Y-%m-%d %H:%M:%S IST")
        bits = [
            f"{f.name}={'PASS' if f.passed else 'FAIL'}({f.detail})"
            for f in self.factors
        ]
        verdict = "ENTRY" if self.admitted else "SKIP"
        return (
            f"{stamp} {verdict} {symbol} |h|={self.hybrid_abs:.3f} "
            f"floor={self.effective_min_hybrid:.2f} " + " ".join(bits)
        )


def _as_ist(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=ist())
    return dt.astimezone(ist())


def clamp_min_hybrid(value: float) -> float:
    """Clamp absolute |hybrid| floor to sensible [0.70, 0.90]."""
    return max(
        DEFAULT_ADAPTIVE_CLAMP_LO,
        min(DEFAULT_ADAPTIVE_CLAMP_HI, float(value)),
    )


def clamp_adaptive_delta(delta: float) -> float:
    """Keep adaptive offset so baseline±delta stays inside clamp band.

    Delta itself is typically in [-0.10, +0.10]; final floor is clamped.
    """
    return max(-0.20, min(0.20, float(delta)))


def session_min_hybrid_abs(
    now: datetime,
    *,
    before_12: float = DEFAULT_MIN_HYBRID_BEFORE_12,
    after_12: float = DEFAULT_MIN_HYBRID_AFTER_12,
    after_1420: float = DEFAULT_MIN_HYBRID_AFTER_1420,
    afternoon_after_ist: str = DEFAULT_AFTERNOON_AFTER_IST,
    late_after_ist: str = DEFAULT_LATE_ENTRY_AFTER_IST,
    adaptive_delta: float = 0.0,
) -> float:
    """Time-of-day |hybrid| floor + adaptive delta, clamped to [0.70, 0.90]."""
    delta = clamp_adaptive_delta(adaptive_delta)
    if at_or_after_hhmm(late_after_ist, now):
        base = float(after_1420)
    elif at_or_after_hhmm(afternoon_after_ist, now):
        base = float(after_12)
    else:
        base = float(before_12)
    return clamp_min_hybrid(base + delta)


def check_hybrid_floor(
    hybrid_score: float,
    now: datetime,
    *,
    adaptive_delta: float = 0.0,
    before_12: float = DEFAULT_MIN_HYBRID_BEFORE_12,
    after_12: float = DEFAULT_MIN_HYBRID_AFTER_12,
    after_1420: float = DEFAULT_MIN_HYBRID_AFTER_1420,
) -> FactorResult:
    floor = session_min_hybrid_abs(
        now,
        before_12=before_12,
        after_12=after_12,
        after_1420=after_1420,
        adaptive_delta=adaptive_delta,
    )
    habs = abs(float(hybrid_score))
    ok = habs + 1e-12 >= floor
    return FactorResult(
        name="hybrid_floor",
        passed=ok,
        detail=f"|h|={habs:.3f} floor={floor:.2f}",
    )


def check_mtf_alignment(
    candles_15m: Sequence[Candle],
    *,
    hybrid_score: float,
    candles_1h: Sequence[Candle] | None = None,
) -> FactorResult:
    """15m and 1h must share bullish/bearish direction matching hybrid sign."""
    bars_1h = list(candles_1h) if candles_1h is not None else aggregate_15m_to_1h(
        candles_15m
    )
    d15 = bar_direction(candles_15m)
    d1h = bar_direction(bars_1h)
    if d15 == "neutral" or d1h == "neutral":
        return FactorResult(
            name="mtf",
            passed=False,
            detail=f"15m={d15} 1h={d1h} (need non-neutral same direction)",
        )
    if d15 != d1h:
        return FactorResult(
            name="mtf",
            passed=False,
            detail=f"15m={d15} ≠ 1h={d1h}",
        )
    if not direction_matches_signal(d15, hybrid_score=hybrid_score):
        return FactorResult(
            name="mtf",
            passed=False,
            detail=f"dir={d15} conflicts hybrid_sign={_sign_label(hybrid_score)}",
        )
    return FactorResult(
        name="mtf",
        passed=True,
        detail=f"15m={d15} 1h={d1h} aligned",
    )


def check_relative_volume(
    candles_15m: Sequence[Candle],
    *,
    mult: float = DEFAULT_RVOL_MULT,
    lookback: int = DEFAULT_RVOL_LOOKBACK,
) -> FactorResult:
    """Last 15m bar volume ≥ mult × mean of prior ``lookback`` bars."""
    need = int(lookback) + 1
    if len(candles_15m) < need:
        return FactorResult(
            name="rvol",
            passed=False,
            detail=f"need ≥{need} bars have={len(candles_15m)}",
        )
    last = float(candles_15m[-1].volume)
    prior = [float(c.volume) for c in candles_15m[-(need):-1]]
    mean = sum(prior) / len(prior) if prior else 0.0
    if mean <= 0:
        return FactorResult(
            name="rvol",
            passed=False,
            detail="prior mean volume=0",
        )
    ratio = last / mean
    ok = ratio + 1e-12 >= float(mult)
    return FactorResult(
        name="rvol",
        passed=ok,
        detail=f"last={last:.0f} mean20={mean:.1f} ratio={ratio:.2f} need≥{mult}",
    )


def check_atr_pct(
    atr14: float | None,
    price: float,
    *,
    min_pct: float = DEFAULT_ATR_MIN_PCT_OF_PRICE,
) -> FactorResult:
    """ATR(14) must exceed ``min_pct``% of price."""
    if atr14 is None or float(atr14) <= 0:
        return FactorResult(name="atr_pct", passed=False, detail="ATR unavailable")
    px = float(price)
    if px <= 0:
        return FactorResult(name="atr_pct", passed=False, detail="price≤0")
    pct = float(atr14) / px * 100.0
    ok = pct + 1e-12 > float(min_pct)
    return FactorResult(
        name="atr_pct",
        passed=ok,
        detail=f"ATR/price={pct:.3f}% need>{min_pct}%",
    )


def check_proximity_block(
    price: float,
    atr14: float | None,
    daily_high: float | None,
    daily_low: float | None,
    *,
    proximity_atr_mult: float = DEFAULT_PROXIMITY_ATR_MULT,
) -> FactorResult:
    """Block entry when price is within ``proximity_atr_mult × ATR`` of daily H/L."""
    if atr14 is None or float(atr14) <= 0:
        # TODO: when daily H/L feed lands without ATR, revisit fail-closed vs open.
        return FactorResult(
            name="proximity",
            passed=False,
            detail="ATR unavailable — proximity fail-closed",
        )
    if daily_high is None or daily_low is None:
        # Prefer fail-closed when session H/L cannot be derived from 15m bars.
        return FactorResult(
            name="proximity",
            passed=False,
            detail="daily H/L unavailable — proximity fail-closed",
        )
    band = float(proximity_atr_mult) * float(atr14)
    px = float(price)
    near_high = abs(float(daily_high) - px) <= band + 1e-12
    near_low = abs(px - float(daily_low)) <= band + 1e-12
    if near_high or near_low:
        which = "high" if near_high else "low"
        return FactorResult(
            name="proximity",
            passed=False,
            detail=(
                f"within {proximity_atr_mult}×ATR of daily {which} "
                f"(band={band:.4f} px={px:.4f} H={daily_high:.4f} L={daily_low:.4f})"
            ),
        )
    return FactorResult(
        name="proximity",
        passed=True,
        detail=f"clear of daily H/L band={band:.4f}",
    )


def _sign_label(score: float) -> str:
    if float(score) > 0:
        return "bullish"
    if float(score) < 0:
        return "bearish"
    return "neutral"


def news_sentiment_1h(
    news_items: Sequence[NewsItem],
    sentiments: Sequence[SentimentResult],
    *,
    now: datetime,
    lookback_minutes: int = DEFAULT_NEWS_CONFLICT_LOOKBACK_MIN,
) -> float | None:
    """Aggregate news score over the last ``lookback_minutes`` (default 1h).

    Returns ``None`` when no recent scored news (gate passes — no conflict).
    """
    now_ist = _as_ist(now)
    cutoff = now_ist - timedelta(minutes=int(lookback_minutes))
    by_id = {s.news_id: s for s in sentiments}
    scores: list[tuple[float, float]] = []
    for item in news_items:
        pub = item.published_at
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=ist())
        else:
            pub = pub.astimezone(ist())
        if pub < cutoff:
            continue
        sent = by_id.get(item.id)
        if sent is None:
            continue
        scores.append((float(sent.score), float(sent.confidence or 0.0)))
    if not scores:
        return None
    conf_sum = sum(c for _, c in scores) or 1e-9
    return sum(s * c for s, c in scores) / conf_sum


def check_news_conflict(
    hybrid_score: float,
    news_score_1h: float | None,
) -> FactorResult:
    """Block when last-1h news sentiment conflicts with signal direction."""
    if news_score_1h is None:
        return FactorResult(
            name="news_1h",
            passed=True,
            detail="no 1h news — skip conflict check",
        )
    ns = float(news_score_1h)
    hs = float(hybrid_score)
    if abs(ns) < 1e-9:
        return FactorResult(name="news_1h", passed=True, detail="news neutral")
    if (hs > 0 and ns < 0) or (hs < 0 and ns > 0):
        return FactorResult(
            name="news_1h",
            passed=False,
            detail=f"news_1h={ns:.3f} conflicts signal={_sign_label(hs)}",
        )
    return FactorResult(
        name="news_1h",
        passed=True,
        detail=f"news_1h={ns:.3f} ok",
    )


def last_trade_times_from_fills(
    fills: Sequence[Mapping[str, Any]] | Iterable[Mapping[str, Any]],
) -> dict[str, datetime]:
    """Map symbol → latest fill timestamp (entry **or** exit) for cooloff.

    Cooloff is after **any** trade on the symbol (not only closes).
    """
    out: dict[str, datetime] = {}
    for fill in fills:
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
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(text, fmt).replace(tzinfo=ist())
            except ValueError:
                continue
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_ist(dt)


def check_cooloff(
    *,
    last_trade: datetime | None,
    now: datetime,
    cooloff_minutes: int = DEFAULT_COOLOFF_MINUTES,
    same_session_only: bool = True,
    session_day: date | None = None,
) -> FactorResult:
    if last_trade is None or int(cooloff_minutes) <= 0:
        return FactorResult(name="cooloff", passed=True, detail="no prior trade")
    close_ist = _as_ist(last_trade)
    now_ist = _as_ist(now)
    if same_session_only:
        close_day = pnl_ist_day(close_ist)
        now_day = session_day if session_day is not None else pnl_ist_day(now_ist)
        if close_day != now_day:
            return FactorResult(
                name="cooloff",
                passed=True,
                detail="prior trade different session day",
            )
    remaining = timedelta(minutes=int(cooloff_minutes)) - (now_ist - close_ist)
    if remaining > timedelta(0):
        mins = remaining.total_seconds() / 60.0
        return FactorResult(
            name="cooloff",
            passed=False,
            detail=f"cooloff {cooloff_minutes}m — {mins:.0f}m left",
        )
    return FactorResult(name="cooloff", passed=True, detail="cooloff clear")


def rolling_equity_win_rate(
    fills: Sequence[Mapping[str, Any]] | Iterable[Mapping[str, Any]],
    *,
    window_trips: int = DEFAULT_WR_WINDOW_TRIPS,
) -> tuple[float | None, int, int]:
    """Win rate over the last ``window_trips`` closed equity round-trips.

    Returns ``(win_rate|None, wins, n)``. ``None`` when n=0.
    Documented window: **last N closed equity round-trips** (default N=20).
    """
    trips = pair_round_trip_trades([dict(f) for f in fills])
    # pair_round_trip_trades returns chronological; take the last N
    recent = trips[-max(0, int(window_trips)) :] if trips else []
    n = len(recent)
    if n == 0:
        return None, 0, 0
    wins = 0
    for t in recent:
        pnl = t.get("pnl")
        try:
            if pnl is not None and float(pnl) > 0:
                wins += 1
        except (TypeError, ValueError):
            continue
    return wins / n, wins, n


def week_equity_win_rate(
    fills: Sequence[Mapping[str, Any]] | Iterable[Mapping[str, Any]],
    *,
    now: datetime,
    lookback_trading_days: int = DEFAULT_ADAPTIVE_LOOKBACK_TRADING_DAYS,
) -> tuple[float | None, int, int]:
    """Win rate for closed trips whose exit falls in the last N trading days.

    Used by adaptive |hybrid| feedback (raise when week WR < 55%).
    """
    from options_portal_domain.calendar import is_nse_trading_day

    now_ist = _as_ist(now)
    # Walk back calendar days until we collected lookback trading days.
    trading_days: set[date] = set()
    cursor = now_ist.date()
    guard = 0
    while len(trading_days) < int(lookback_trading_days) and guard < 21:
        if is_nse_trading_day(cursor):
            trading_days.add(cursor)
        cursor = cursor - timedelta(days=1)
        guard += 1

    trips = pair_round_trip_trades([dict(f) for f in fills])
    wins = 0
    n = 0
    for t in trips:
        disp = str(t.get("exit_ts_ist_display") or "")
        day_s = disp[:10] if len(disp) >= 10 else ""
        try:
            day = date.fromisoformat(day_s)
        except ValueError:
            continue
        if day not in trading_days:
            continue
        n += 1
        try:
            pnl = t.get("pnl")
            if pnl is not None and float(pnl) > 0:
                wins += 1
        except (TypeError, ValueError):
            pass
    if n == 0:
        return None, 0, 0
    return wins / n, wins, n


def effective_max_open_symbols(
    win_rate: float | None,
    *,
    max_open: int = DEFAULT_MAX_OPEN,
    max_open_low_wr: int = DEFAULT_MAX_OPEN_LOW_WR,
    wr_threshold: float = DEFAULT_WR_THRESHOLD,
    min_trips_for_wr: int = 5,
    trip_count: int = 0,
) -> int:
    """Max open 6 normally; 4 when rolling WR < 50% (enough sample)."""
    hi = max(0, int(max_open))
    lo = max(0, min(int(max_open_low_wr), hi))
    if win_rate is None or int(trip_count) < int(min_trips_for_wr):
        return hi
    if float(win_rate) < float(wr_threshold):
        return lo
    return hi


def adaptive_delta_from_week_wr(
    week_wr: float | None,
    current_delta: float = 0.0,
    *,
    threshold: float = DEFAULT_ADAPTIVE_WEEK_WR_THRESHOLD,
    step: float = DEFAULT_ADAPTIVE_STEP,
) -> float:
    """Adjust adaptive delta: raise floor when week WR < 55%, else ease toward 0.

    - week WR < threshold → ``current_delta + step`` (stricter)
    - week WR ≥ threshold → ``current_delta - step`` (loosen toward baseline)
    - week WR unknown → keep current
    Clamped via ``clamp_adaptive_delta``.
    """
    cur = clamp_adaptive_delta(current_delta)
    if week_wr is None:
        return cur
    if float(week_wr) < float(threshold):
        return clamp_adaptive_delta(cur + float(step))
    return clamp_adaptive_delta(cur - float(step))


def trend_confirmed_for_overnight(
    *,
    open_side: str | None,
    hybrid_score: float,
    dir_15m: Direction,
    dir_1h: Direction,
    min_abs: float = DEFAULT_OVERNIGHT_HOLD_MIN_HYBRID,
) -> bool:
    """Overnight hold: |hybrid| ≥ min + same-side + 15m/1h trend confirmed."""
    if open_side is None:
        return False
    side = str(open_side).strip().lower()
    hs = float(hybrid_score)
    if abs(hs) + 1e-12 < float(min_abs):
        return False
    if side == "long" and hs < float(min_abs):
        return False
    if side == "short" and hs > -float(min_abs):
        return False
    if dir_15m != dir_1h or dir_15m == "neutral":
        return False
    if side == "long" and dir_15m != "bullish":
        return False
    if side == "short" and dir_15m != "bearish":
        return False
    return True


def min_profit_for_rrr(
    stop_distance: float,
    *,
    min_rrr: float = DEFAULT_MIN_RRR,
) -> float:
    """Minimum profit distance for discretionary exits (profit ≥ RRR × risk)."""
    return abs(float(min_rrr)) * abs(float(stop_distance))


def evaluate_entry_gate(
    *,
    symbol: str,
    hybrid_score: float,
    now: datetime,
    candles_15m: Sequence[Candle],
    atr14: float | None,
    price: float,
    news_items: Sequence[NewsItem] = (),
    sentiments: Sequence[SentimentResult] = (),
    last_trade: datetime | None = None,
    is_index: bool = False,
    in_entry_window: bool = True,
    adaptive_delta: float = 0.0,
    cfg: Mapping[str, Any] | None = None,
) -> GateResult:
    """Run all high-conviction entry factors; admit only if every factor passes."""
    if cfg and "min_hybrid_before_12" in cfg:
        hc = {**high_conviction_config(None), **dict(cfg)}
    elif cfg and ("trading" in cfg or "risk" in (cfg.get("trading") or {})):
        hc = high_conviction_config(dict(cfg))
    elif cfg:
        # Treat as risk.high_conviction fragment or flat overrides.
        wrapped = {"trading": {"risk": {"high_conviction": dict(cfg)}}}
        if any(k.startswith("min_hybrid") for k in cfg):
            hc = {**high_conviction_config(None), **dict(cfg)}
        else:
            hc = high_conviction_config(wrapped)
    else:
        hc = high_conviction_config(None)

    factors: list[FactorResult] = []

    if is_index:
        factors.append(
            FactorResult(name="index_block", passed=False, detail="index_not_equity")
        )
    else:
        factors.append(FactorResult(name="index_block", passed=True, detail="stock"))

    if in_entry_window:
        factors.append(
            FactorResult(name="entry_window", passed=True, detail="in 09:15–15:20")
        )
    else:
        factors.append(
            FactorResult(
                name="entry_window",
                passed=False,
                detail="outside entry window",
            )
        )

    hybrid_f = check_hybrid_floor(
        hybrid_score,
        now,
        adaptive_delta=adaptive_delta,
        before_12=float(hc["min_hybrid_before_12"]),
        after_12=float(hc["min_hybrid_after_12"]),
        after_1420=float(hc["min_hybrid_after_1420"]),
    )
    factors.append(hybrid_f)
    floor = session_min_hybrid_abs(
        now,
        before_12=float(hc["min_hybrid_before_12"]),
        after_12=float(hc["min_hybrid_after_12"]),
        after_1420=float(hc["min_hybrid_after_1420"]),
        adaptive_delta=adaptive_delta,
    )

    factors.append(
        check_mtf_alignment(candles_15m, hybrid_score=hybrid_score)
    )
    factors.append(
        check_relative_volume(
            candles_15m,
            mult=float(hc["rvol_mult"]),
            lookback=int(hc["rvol_lookback"]),
        )
    )
    factors.append(
        check_atr_pct(
            atr14,
            price,
            min_pct=float(hc["atr_min_pct_of_price"]),
        )
    )

    day = pnl_ist_day(_as_ist(now))
    hl = session_daily_high_low(candles_15m, session_day=day, now=now)
    daily_high = hl[0] if hl else None
    daily_low = hl[1] if hl else None
    factors.append(
        check_proximity_block(
            price,
            atr14,
            daily_high,
            daily_low,
            proximity_atr_mult=float(hc["proximity_atr_mult"]),
        )
    )

    news_1h = news_sentiment_1h(
        news_items,
        sentiments,
        now=now,
        lookback_minutes=int(hc["news_lookback_minutes"]),
    )
    factors.append(check_news_conflict(hybrid_score, news_1h))
    factors.append(
        check_cooloff(
            last_trade=last_trade,
            now=now,
            cooloff_minutes=int(hc["cooloff_minutes"]),
            same_session_only=bool(hc["cooloff_same_session"]),
            session_day=day,
        )
    )

    admitted = all(f.passed for f in factors)
    failing = [f.name for f in factors if not f.passed]
    reason = (
        "all_factors_pass"
        if admitted
        else "fail:" + ",".join(failing)
    )
    return GateResult(
        admitted=admitted,
        factors=tuple(factors),
        effective_min_hybrid=floor,
        hybrid_abs=abs(float(hybrid_score)),
        reason=reason,
    )


def high_conviction_config(cfg: dict[str, Any] | None) -> dict[str, Any]:
    """Read ``trading.risk.high_conviction`` (+ session flatten keys for docs)."""
    root = cfg or {}
    trading = dict(root.get("trading") or {})
    risk = dict(trading.get("risk") or {})
    session = dict(trading.get("session") or {})
    raw = dict(risk.get("high_conviction") or {})
    floors = dict(raw.get("hybrid_floors") or {})
    rvol = dict(raw.get("relative_volume") or {})
    atr = dict(raw.get("atr") or {})
    prox = dict(raw.get("proximity") or {})
    news = dict(raw.get("news") or {})
    cool = dict(raw.get("cooloff") or {})
    cap = dict(raw.get("capacity") or {})
    wr = dict(raw.get("win_rate") or {})
    adaptive = dict(raw.get("adaptive") or {})
    exit_cfg = dict(raw.get("exit") or {})

    return {
        "enabled": bool(raw.get("enabled", True)),
        "min_hybrid_before_12": float(
            floors.get("before_12_ist", DEFAULT_MIN_HYBRID_BEFORE_12)
        ),
        "min_hybrid_after_12": float(
            floors.get("after_12_ist", DEFAULT_MIN_HYBRID_AFTER_12)
        ),
        "min_hybrid_after_1420": float(
            floors.get("after_1420_ist", DEFAULT_MIN_HYBRID_AFTER_1420)
        ),
        "afternoon_after_ist": str(
            floors.get("afternoon_after_ist", DEFAULT_AFTERNOON_AFTER_IST)
        ),
        "late_after_ist": str(
            floors.get("late_after_ist", DEFAULT_LATE_ENTRY_AFTER_IST)
        ),
        "rvol_mult": float(rvol.get("mult", DEFAULT_RVOL_MULT)),
        "rvol_lookback": int(rvol.get("lookback_bars", DEFAULT_RVOL_LOOKBACK)),
        "atr_period": int(atr.get("period", DEFAULT_ATR_PERIOD)),
        "atr_min_pct_of_price": float(
            atr.get("min_pct_of_price", DEFAULT_ATR_MIN_PCT_OF_PRICE)
        ),
        "proximity_atr_mult": float(
            prox.get("atr_mult", DEFAULT_PROXIMITY_ATR_MULT)
        ),
        "news_lookback_minutes": int(
            news.get("lookback_minutes", DEFAULT_NEWS_CONFLICT_LOOKBACK_MIN)
        ),
        "cooloff_minutes": int(cool.get("minutes", DEFAULT_COOLOFF_MINUTES)),
        "cooloff_same_session": bool(cool.get("same_session_only", True)),
        "max_open": int(cap.get("max_open", DEFAULT_MAX_OPEN)),
        "max_open_low_wr": int(cap.get("max_open_low_wr", DEFAULT_MAX_OPEN_LOW_WR)),
        "wr_threshold": float(wr.get("threshold", DEFAULT_WR_THRESHOLD)),
        "wr_window_trips": int(wr.get("window_trips", DEFAULT_WR_WINDOW_TRIPS)),
        "wr_min_trips": int(wr.get("min_trips", 5)),
        "adaptive_enabled": bool(adaptive.get("enabled", True)),
        "adaptive_week_wr_threshold": float(
            adaptive.get("week_wr_threshold", DEFAULT_ADAPTIVE_WEEK_WR_THRESHOLD)
        ),
        "adaptive_step": float(adaptive.get("step", DEFAULT_ADAPTIVE_STEP)),
        "adaptive_clamp_lo": float(
            adaptive.get("clamp_lo", DEFAULT_ADAPTIVE_CLAMP_LO)
        ),
        "adaptive_clamp_hi": float(
            adaptive.get("clamp_hi", DEFAULT_ADAPTIVE_CLAMP_HI)
        ),
        "adaptive_lookback_trading_days": int(
            adaptive.get(
                "lookback_trading_days", DEFAULT_ADAPTIVE_LOOKBACK_TRADING_DAYS
            )
        ),
        "min_rrr": float(exit_cfg.get("min_rrr", DEFAULT_MIN_RRR)),
        "no_new_entries_after": str(
            session.get("no_new_entries_after", DEFAULT_NO_NEW_ENTRIES_AFTER)
        ),
        "soft_flatten_ist": str(
            session.get("soft_flatten_ist", DEFAULT_SOFT_FLATTEN_IST)
        ),
        "overnight_hold_min_hybrid_abs": float(
            session.get(
                "overnight_hold_min_hybrid_abs", DEFAULT_OVERNIGHT_HOLD_MIN_HYBRID
            )
        ),
    }


@dataclass
class SkipEntryLog:
    """In-memory skip/entry counters for daily summary (process-local)."""

    entries: int = 0
    skips: int = 0
    skip_reasons: dict[str, int] = field(default_factory=dict)

    def record(self, gate: GateResult) -> None:
        if gate.admitted:
            self.entries += 1
            return
        self.skips += 1
        for f in gate.failing:
            self.skip_reasons[str(f.name)] = self.skip_reasons.get(str(f.name), 0) + 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "entries": self.entries,
            "skips": self.skips,
            "skip_reasons": dict(self.skip_reasons),
        }


# Process-local counters (worker); reset each IST day if desired by caller.
_SKIP_LOG = SkipEntryLog()


def skip_entry_log() -> SkipEntryLog:
    return _SKIP_LOG


def reset_skip_entry_log() -> None:
    global _SKIP_LOG
    _SKIP_LOG = SkipEntryLog()
