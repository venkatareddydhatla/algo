"""Aggregate 15m OHLCV into 1h bars and session daily high/low.

Prefer real aggregation from existing 15m candles over a separate 1h broker
fetch (Upstox ``Timeframe`` historically lacked ``1h``). Daily H/L for the
proximity gate uses today's IST session bars when available.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timezone
from typing import Literal

from options_portal_domain.tz import ist
from options_portal_domain.types import Candle

Direction = Literal["bullish", "bearish", "neutral"]


def _as_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _as_ist(ts: datetime) -> datetime:
    return _as_utc(ts).astimezone(ist())


def _hour_bucket_key(ts: datetime) -> tuple[date, int]:
    """IST calendar date + hour for 1h aggregation buckets."""
    local = _as_ist(ts)
    return local.date(), local.hour


def aggregate_15m_to_1h(candles: Sequence[Candle]) -> list[Candle]:
    """Collapse contiguous 15m bars into 1h OHLCV (IST hour buckets).

    Volume sums; OI takes the last non-None. Empty input → ``[]``.
    """
    if not candles:
        return []
    buckets: dict[tuple[date, int], list[Candle]] = {}
    order: list[tuple[date, int]] = []
    for bar in candles:
        key = _hour_bucket_key(bar.ts)
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(bar)

    out: list[Candle] = []
    for key in order:
        group = buckets[key]
        first = group[0]
        last = group[-1]
        oi = None
        for g in reversed(group):
            if g.oi is not None:
                oi = g.oi
                break
        # Bar open ts = first 15m open (UTC-aware preserved via first.ts)
        out.append(
            Candle(
                symbol=first.symbol,
                timeframe="1h",  # type: ignore[arg-type]
                ts=first.ts,
                open=float(first.open),
                high=max(float(g.high) for g in group),
                low=min(float(g.low) for g in group),
                close=float(last.close),
                volume=int(sum(int(g.volume) for g in group)),
                oi=oi,
            )
        )
    return out


def session_daily_high_low(
    candles: Sequence[Candle],
    *,
    session_day: date | None = None,
    now: datetime | None = None,
) -> tuple[float, float] | None:
    """Major daily high/low from 15m (or any) bars on the IST session day.

    Returns ``(high, low)`` or ``None`` when no bars fall on that day.
    """
    if not candles:
        return None
    if session_day is None:
        clock = now
        if clock is None:
            clock = datetime.now(tz=ist())
        session_day = _as_ist(clock).date()
    highs: list[float] = []
    lows: list[float] = []
    for bar in candles:
        if _as_ist(bar.ts).date() != session_day:
            continue
        highs.append(float(bar.high))
        lows.append(float(bar.low))
    if not highs:
        return None
    return max(highs), min(lows)


def bar_direction(
    candles: Sequence[Candle],
    *,
    min_bars: int = 2,
) -> Direction:
    """Simple trend direction: last close vs prior close (neutral if flat/short).

    Used for multi-timeframe alignment (15m vs aggregated 1h). Neutral when
    insufficient bars or zero change.
    """
    if len(candles) < int(min_bars):
        return "neutral"
    prev = float(candles[-2].close)
    last = float(candles[-1].close)
    if last > prev:
        return "bullish"
    if last < prev:
        return "bearish"
    return "neutral"


def direction_matches_signal(
    direction: Direction,
    *,
    hybrid_score: float,
) -> bool:
    """True when bar direction agrees with hybrid sign (long/short)."""
    if direction == "neutral":
        return False
    if float(hybrid_score) > 0:
        return direction == "bullish"
    if float(hybrid_score) < 0:
        return direction == "bearish"
    return False
