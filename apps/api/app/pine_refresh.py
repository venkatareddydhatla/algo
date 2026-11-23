"""API-side Reversal Pro refresh so Sunday/after-hours still flips Action.

GET /signals previously only read stale ``pine.signal.*`` KV. When the worker
is idle (after hours), that left VEDL Action=BUY + Trend=BEARISH even after
tip #66. This module recomputes from live 15m candles on read and enforces
the hard invariant: Trend BEARISH + lastConfirmedPivotIsHigh → Action SELL.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from options_portal_domain.pine_signals import (
    TREND_BEARISH,
    evaluate_pine_signal,
    enforce_bearish_pivot_sell_action,
    pine_params_from_config,
)

logger = logging.getLogger(__name__)


def _norm_action(action: Any) -> str:
    act = str(action or "HOLD").upper()
    if act in {"BUY_CE_BIAS", "BUY_CE", "LONG"}:
        return "BUY"
    if act in {"BUY_PE_BIAS", "BUY_PE", "SHORT"}:
        return "SELL"
    if act not in {"BUY", "SELL", "HOLD"}:
        return "HOLD"
    return act


def _norm_trend(trend: Any) -> str:
    t = str(trend or "NEUTRAL").strip().upper()
    if t in {"BULLISH", "BULL", "UP"}:
        return "BULLISH"
    if t in {"BEARISH", "BEAR", "DOWN"}:
        return "BEARISH"
    return "NEUTRAL"


def row_needs_candle_recompute(row: Mapping[str, Any]) -> bool:
    """True when KV/placeholder may be stale vs chart (esp. BUY+BEARISH).

    Also forces refresh for HOLD (customs stuck on hybrid-era HOLD) and any
    non-Reversal-Pro engine so Action/Trend never linger from old hybrid KV.
    """
    action = _norm_action(row.get("action") or row.get("pine_action"))
    trend = _norm_trend(row.get("trend"))
    feats = dict(row.get("features") or {})
    pivot = feats.get("last_confirmed_pivot_is_high")
    if pivot is None:
        pivot = feats.get("lastConfirmedPivotIsHigh")
    if action == "HOLD":
        return True
    engine = str(
        row.get("signal_engine") or feats.get("signal_engine") or ""
    ).lower()
    if engine and engine != "reversal_pro_v32":
        return True
    if action == "BUY" and trend == TREND_BEARISH:
        return True
    if trend == TREND_BEARISH and pivot is None:
        return True
    if not row.get("pine_persisted"):
        return True
    return False


def apply_pine_result_to_row(
    row: dict[str, Any],
    result: Any,
    *,
    bar_ts: datetime | None = None,
) -> dict[str, Any]:
    """Overwrite watchlist Action/Trend from a fresh ``evaluate_pine_signal``."""
    action = _norm_action(result.action.value if hasattr(result.action, "value") else result.action)
    trend = _norm_trend(getattr(result, "trend", None) or result.features.get("trend"))
    feats = {
        k: v
        for k, v in dict(result.features or {}).items()
        if isinstance(v, (int, float, bool, str, type(None)))
    }
    action = enforce_bearish_pivot_sell_action(
        action,
        trend,
        features=feats,
        last_confirmed_pivot_is_high=getattr(
            getattr(result, "state", None), "last_confirmed_pivot_is_high", None
        ),
    )
    row["action"] = action
    row["pine_action"] = action
    row["display_action"] = action
    row["trend"] = trend
    row["hybrid_score"] = float(result.score)
    row["tech_score"] = float(result.score)
    row["confidence"] = float(result.confidence)
    row["reasons"] = list(result.reasons)
    row["reason_kind"] = "pine"
    row["reason_summary"] = result.reason_summary
    row["reason_detail"] = result.reason_detail
    row["features"] = feats
    row["action_source"] = getattr(result, "action_source", feats.get("action_source"))
    row["signal_engine"] = "reversal_pro_v32"
    row["pine_persisted"] = True
    row["pine_recomputed"] = True
    row["dry_run_synthetic"] = False
    row["stub"] = False
    row["updated_at"] = datetime.now(tz=timezone.utc).isoformat()
    if bar_ts is not None:
        ts = bar_ts
        if getattr(ts, "tzinfo", None) is None:
            ts = ts.replace(tzinfo=timezone.utc)
        row["bar_ts"] = ts.astimezone(timezone.utc).isoformat()
    return row


def persist_recomputed_pine_row(row: Mapping[str, Any]) -> None:
    """Rewrite ``pine.signal.*`` KV from an API-side recompute (clear stale BUY)."""
    sym = str(row.get("symbol") or "").strip().upper()
    if not sym:
        return
    try:
        from options_portal_persistence.kv_store import KvStore

        kv = KvStore()
        if not kv.available:
            return
        key = f"pine.signal.{sym}"
        action = _norm_action(row.get("action"))
        # Drop prior BUY so stale U1 sticky cannot linger after flip to SELL.
        try:
            prev = kv.get(key)
            if (
                isinstance(prev, dict)
                and _norm_action(prev.get("action")) == "BUY"
                and action == "SELL"
            ):
                kv.delete(key)
        except Exception:  # noqa: BLE001
            pass
        kv.set(
            key,
            {
                "symbol": sym,
                "action": action,
                "trend": _norm_trend(row.get("trend")),
                "buy_signal": bool((row.get("features") or {}).get("buy_signal")),
                "sell_signal": bool((row.get("features") or {}).get("sell_signal")),
                "action_source": row.get("action_source") or "pivot",
                "score": float(row.get("hybrid_score") or row.get("tech_score") or 0.0),
                "confidence": float(row.get("confidence") or 0.0),
                "reasons": list(row.get("reasons") or []),
                "reason_kind": "pine",
                "reason_summary": row.get("reason_summary"),
                "reason_detail": row.get("reason_detail"),
                "features": {
                    **dict(row.get("features") or {}),
                    "action_no_hold": True,
                    "signal_engine": "reversal_pro_v32",
                    "pine": True,
                },
                "bar_ts": row.get("bar_ts"),
                "updated_at": row.get("updated_at")
                or datetime.now(tz=timezone.utc).isoformat(),
                "timeframe": str(row.get("timeframe") or "15m"),
                "signal_engine": "reversal_pro_v32",
            },
        )
    except Exception:  # noqa: BLE001
        logger.exception("persist recomputed pine.signal.%s failed", sym)


def enforce_bearish_pivot_invariant(rows: Sequence[dict[str, Any]]) -> int:
    """Screenshot guarantee: no BUY+BEARISH+pivot-high leaves the API.

    Mutates ``action`` / ``pine_action`` / ``display_action`` in place.
    Returns how many rows were forced to SELL.
    """
    fixed = 0
    for row in rows:
        trend = _norm_trend(row.get("trend"))
        feats = dict(row.get("features") or {})
        before = _norm_action(row.get("action") or row.get("pine_action"))
        after = enforce_bearish_pivot_sell_action(before, trend, features=feats)
        if after != before:
            row["action"] = after
            row["pine_action"] = after
            row["display_action"] = after
            row["action_source"] = "pivot"
            row["invariant_forced_sell"] = True
            fixed += 1
        elif after == "SELL" and trend == TREND_BEARISH:
            # Keep pine_action aligned even when action already SELL.
            row["pine_action"] = "SELL"
            if row.get("display_action") == "BUY" and not row.get(
                "action_from_open_trade"
            ):
                row["display_action"] = "SELL"
                row["action"] = "SELL"
    return fixed


async def refresh_rows_from_candles(
    rows: list[dict[str, Any]],
    *,
    cfg: Mapping[str, Any] | None = None,
    force_all: bool = False,
) -> dict[str, Any]:
    """Recompute Pine Action/Trend from live 15m candles when needed.

    Runs on GET /signals so after-hours (worker idle) still updates VEDL.
    Without Upstox token / candles, falls through to feature invariant only.
    """
    if not rows:
        return {"recomputed": [], "attempted": [], "candle_symbols": 0}
    need: list[str] = []
    for row in rows:
        sym = str(row.get("symbol") or "").strip().upper()
        if not sym:
            continue
        if force_all or row_needs_candle_recompute(row):
            need.append(sym)
    need = sorted(set(need))
    if not need:
        return {"recomputed": [], "attempted": [], "candle_symbols": 0}

    from app.marks import _live_15m_candles_map

    candles_by_sym = await _live_15m_candles_map(need)
    params = pine_params_from_config(cfg)
    recomputed: list[str] = []
    for row in rows:
        sym = str(row.get("symbol") or "").strip().upper()
        if sym not in need:
            continue
        candles = candles_by_sym.get(sym) or []
        if not candles:
            continue
        try:
            result = evaluate_pine_signal(
                sym, candles, params=params, prefer_closed=True
            )
            closed = candles
            try:
                from options_portal_domain.pine_signals import prefer_closed_candles

                closed = prefer_closed_candles(candles) or list(candles)
            except Exception:  # noqa: BLE001
                closed = list(candles)
            bar_ts = closed[-1].ts if closed else None
            apply_pine_result_to_row(row, result, bar_ts=bar_ts)
            persist_recomputed_pine_row(row)
            recomputed.append(sym)
        except Exception:  # noqa: BLE001
            logger.exception("API pine recompute failed for %s", sym)
    return {
        "recomputed": recomputed,
        "attempted": need,
        "candle_symbols": len(candles_by_sym),
    }
