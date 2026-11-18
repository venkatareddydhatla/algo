"""Persist adaptive |hybrid| floor delta (SQLite kv / config override).

Daily summary (or explicit call) adjusts min |hybrid| by ±0.05 from baseline
based on past-week win rate — raise when week WR < 55%. Clamped so absolute
floors stay in [0.70, 0.90].
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Mapping, Sequence

from options_portal_domain.high_conviction import (
    KV_KEY_ADAPTIVE_DELTA,
    adaptive_delta_from_week_wr,
    clamp_adaptive_delta,
    high_conviction_config,
    week_equity_win_rate,
)
from options_portal_domain.tz import now_ist

logger = logging.getLogger(__name__)


def load_adaptive_delta(
    *,
    cfg: dict[str, Any] | None = None,
    kv: Any | None = None,
) -> float:
    """Load adaptive delta: YAML override → SQLite kv → 0.0."""
    hc = high_conviction_config(cfg)
    trading = dict((cfg or {}).get("trading") or {})
    risk = dict(trading.get("risk") or {})
    raw = dict(risk.get("high_conviction") or {})
    adaptive = dict(raw.get("adaptive") or {})
    if "delta_override" in adaptive and adaptive["delta_override"] is not None:
        return clamp_adaptive_delta(float(adaptive["delta_override"]))

    store = kv
    if store is None:
        try:
            from options_portal_persistence.kv_store import KvStore

            store = KvStore()
        except Exception:  # noqa: BLE001
            logger.exception("adaptive: kv unavailable")
            store = None
    if store is not None and getattr(store, "available", False):
        val = store.get(KV_KEY_ADAPTIVE_DELTA, None)
        if val is not None:
            try:
                return clamp_adaptive_delta(float(val))
            except (TypeError, ValueError):
                pass
    return 0.0


def save_adaptive_delta(delta: float, *, kv: Any | None = None) -> bool:
    store = kv
    if store is None:
        try:
            from options_portal_persistence.kv_store import KvStore

            store = KvStore()
        except Exception:  # noqa: BLE001
            return False
    if store is None or not getattr(store, "available", False):
        return False
    return bool(store.set(KV_KEY_ADAPTIVE_DELTA, clamp_adaptive_delta(delta)))


def run_adaptive_feedback(
    fills: Sequence[Mapping[str, Any]],
    *,
    cfg: dict[str, Any] | None = None,
    now: datetime | None = None,
    kv: Any | None = None,
) -> dict[str, Any]:
    """Adjust adaptive delta from week WR; persist when enabled.

    Returns status dict for daily-summary / logs.
    """
    hc = high_conviction_config(cfg)
    if not hc.get("adaptive_enabled", True):
        return {"updated": False, "reason": "adaptive_disabled"}

    clock = now or now_ist()
    week_wr, wins, n = week_equity_win_rate(
        fills,
        now=clock,
        lookback_trading_days=int(hc["adaptive_lookback_trading_days"]),
    )
    current = load_adaptive_delta(cfg=cfg, kv=kv)
    new_delta = adaptive_delta_from_week_wr(
        week_wr,
        current,
        threshold=float(hc["adaptive_week_wr_threshold"]),
        step=float(hc["adaptive_step"]),
    )
    saved = False
    if new_delta != current:
        saved = save_adaptive_delta(new_delta, kv=kv)
    logger.info(
        "Adaptive |hybrid| feedback: week_wr=%s (%d/%d) delta %.3f → %.3f saved=%s",
        f"{week_wr:.1%}" if week_wr is not None else "n/a",
        wins,
        n,
        current,
        new_delta,
        saved,
    )
    return {
        "updated": saved or new_delta == current,
        "week_wr": week_wr,
        "wins": wins,
        "trips": n,
        "delta_before": current,
        "delta_after": new_delta,
        "saved": saved,
        "reason": "ok",
    }
