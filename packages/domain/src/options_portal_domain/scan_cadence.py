"""Worker full-universe scan cadence — prefer 15m bar-close triggers.

With ``volatile_fno50`` (~19 TradingView names + customs) a full Pine Action +
entry/exit pass should finish well inside one 15m bucket. Bar-close mode waits
for each completed candle (plus a short settle for broker history), then
rescans the **entire** active watchlist once. Mid-bar continuous rescans are
off by default so the same closed bar is not re-scored dozens of times.

Fallback: fixed ``WORKER_INTERVAL_SEC`` sleep after each cycle when
``scan_on_bar_close`` is disabled.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from options_portal_domain.pine_signals import (
    DEFAULT_TIMEFRAME_MINUTES,
    floor_to_timeframe,
    latest_closed_bar_open,
)

# Short pause after the boundary so Upstox historical candles include the close.
DEFAULT_BAR_CLOSE_SETTLE_SEC = 8.0
DEFAULT_FALLBACK_INTERVAL_SEC = 60


def _ensure_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def scan_cadence_config(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read bar-close scan knobs from ``trading`` (+ env overrides)."""
    trading = dict((cfg or {}).get("trading") or {})
    raw_tf = trading.get("timeframe", "15m")
    if isinstance(raw_tf, str) and raw_tf.endswith("m"):
        try:
            tf = max(1, int(raw_tf[:-1]))
        except ValueError:
            tf = DEFAULT_TIMEFRAME_MINUTES
    elif isinstance(raw_tf, (int, float)):
        tf = max(1, int(raw_tf))
    else:
        tf = DEFAULT_TIMEFRAME_MINUTES

    on_bar = bool(trading.get("scan_on_bar_close", True))
    env_on = os.environ.get("WORKER_SCAN_ON_BAR_CLOSE")
    if env_on is not None:
        on_bar = env_on.strip().lower() in {"1", "true", "yes", "on"}

    settle = float(
        trading.get("bar_close_settle_sec", DEFAULT_BAR_CLOSE_SETTLE_SEC)
    )
    env_settle = os.environ.get("WORKER_BAR_CLOSE_SETTLE_SEC")
    if env_settle is not None:
        try:
            settle = float(env_settle)
        except ValueError:
            pass
    settle = max(0.0, settle)

    interval = int(
        os.environ.get(
            "WORKER_INTERVAL_SEC",
            str(
                trading.get(
                    "scan_interval_sec", DEFAULT_FALLBACK_INTERVAL_SEC
                )
            ),
        )
    )
    interval = max(1, interval)

    return {
        "on_bar_close": on_bar,
        "timeframe_minutes": tf,
        "settle_sec": settle,
        "fallback_interval_sec": interval,
    }


def next_bar_close_at(
    now: datetime | None = None,
    *,
    timeframe_minutes: int = DEFAULT_TIMEFRAME_MINUTES,
) -> datetime:
    """UTC instant when the currently forming bar closes (or now if on boundary).

    Exactly on a bucket open (previous bar just closed): returns ``now`` so a
    scan can start immediately (after settle). Mid-bar: returns forming bar end.
    """
    clock = _ensure_utc(now or datetime.now(tz=timezone.utc))
    tf = max(1, int(timeframe_minutes))
    bucket = floor_to_timeframe(clock, timeframe_minutes=tf)
    if clock == bucket:
        return clock
    return bucket + timedelta(minutes=tf)


def seconds_until_bar_close_scan(
    now: datetime | None = None,
    *,
    timeframe_minutes: int = DEFAULT_TIMEFRAME_MINUTES,
    settle_sec: float = DEFAULT_BAR_CLOSE_SETTLE_SEC,
    last_closed_scanned: datetime | None = None,
) -> float:
    """Seconds to sleep before the next full-list bar-close scan.

    - First call (``last_closed_scanned is None``): ``0`` — immediate catch-up
      of the full watchlist (latest closed bar under prefer_closed).
    - After scanning closed bar B: wait until B's successor closes + settle.
    - If a newer bar already closed (slow prior pass / restart): ``0`` or
      remaining settle only.
    """
    clock = _ensure_utc(now or datetime.now(tz=timezone.utc))
    tf = max(1, int(timeframe_minutes))
    settle = max(0.0, float(settle_sec))
    closed_open = latest_closed_bar_open(clock, timeframe_minutes=tf)

    if last_closed_scanned is None:
        return 0.0

    last = _ensure_utc(last_closed_scanned)
    if closed_open > last:
        # New bar already closed — wait only for settle after that close.
        bar_end = closed_open + timedelta(minutes=tf)
        ready_at = bar_end + timedelta(seconds=settle)
        return max(0.0, (ready_at - clock).total_seconds())

    # Still on the same closed bar — wait for the next boundary + settle.
    forming_open = floor_to_timeframe(clock, timeframe_minutes=tf)
    next_close = forming_open + timedelta(minutes=tf)
    ready_at = next_close + timedelta(seconds=settle)
    return max(0.0, (ready_at - clock).total_seconds())


def bar_close_scan_ready(
    now: datetime | None = None,
    *,
    timeframe_minutes: int = DEFAULT_TIMEFRAME_MINUTES,
    settle_sec: float = DEFAULT_BAR_CLOSE_SETTLE_SEC,
    last_closed_scanned: datetime | None = None,
) -> bool:
    """True when a full-list scan should start for the latest closed bar."""
    return (
        seconds_until_bar_close_scan(
            now,
            timeframe_minutes=timeframe_minutes,
            settle_sec=settle_sec,
            last_closed_scanned=last_closed_scanned,
        )
        <= 0.0
    )
