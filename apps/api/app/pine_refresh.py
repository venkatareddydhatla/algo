"""API-side Reversal Pro refresh so Sunday/after-hours still flips Action.

GET /signals previously only read stale ``pine.signal.*`` KV. When the worker
is idle (after hours / morning before first scan), that left symbols like
VEDL (BUY+BEARISH) and BAJFINANCE (BUY+BULLISH) stuck on an older U1 while
the last confirmed Reversal Pro pivot was already a high (SELL). This module
recomputes from live 15m candles on read and enforces: last confirmed pivot
high → Action SELL / low → BUY (independent of Trend).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from options_portal_domain.pine_signals import (
    TREND_BEARISH,
    evaluate_pine_signal,
    enforce_pivot_aligned_action,
    pine_params_from_config,
    pine_signal_bar_is_current,
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


def _feature_pivot(feats: Mapping[str, Any]) -> bool | None:
    pivot = feats.get("last_confirmed_pivot_is_high")
    if pivot is None:
        pivot = feats.get("lastConfirmedPivotIsHigh")
    if pivot is None:
        return None
    if isinstance(pivot, bool):
        return pivot
    text = str(pivot).strip().lower()
    if text in {"1", "true", "yes"}:
        return True
    if text in {"0", "false", "no"}:
        return False
    return None


def row_needs_candle_recompute(row: Mapping[str, Any]) -> bool:
    """True when KV/placeholder may be stale vs chart (pivot / bar mismatch).

    GET ``/signals`` uses ``force_all=True`` so the whole watchlist refreshes
    every read; this helper remains for targeted callers / tests.

    Forces refresh for:
      - HOLD / non-Reversal-Pro engine / missing pine_persisted
      - BUY+BEARISH (VEDL) or Action vs last pivot mismatch (any symbol)
      - Stale ``bar_ts`` (action_no_hold keeps BUY forever otherwise)
      - Missing pivot while Action is BUY/SELL (cannot prove last pivot)
    """
    action = _norm_action(row.get("action") or row.get("pine_action"))
    trend = _norm_trend(row.get("trend"))
    feats = dict(row.get("features") or {})
    pivot = _feature_pivot(feats)
    if action == "HOLD":
        return True
    engine = str(
        row.get("signal_engine") or feats.get("signal_engine") or ""
    ).lower()
    if engine and engine != "reversal_pro_v32":
        return True
    if action == "BUY" and trend == TREND_BEARISH:
        return True
    # Action disagrees with last confirmed pivot (board-wide sticky bug).
    if action == "BUY" and pivot is True:
        return True
    if action == "SELL" and pivot is False:
        return True
    # Without pivot evidence, sticky Action can blanket the watchlist — refresh.
    if action in {"BUY", "SELL"} and pivot is None:
        return True
    if trend == TREND_BEARISH and pivot is None:
        return True
    if not row.get("pine_persisted"):
        return True
    # action_no_hold keeps stale BUY/SELL across bars; recompute when bar lags.
    if not pine_signal_bar_is_current(row.get("bar_ts")):
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
    action = enforce_pivot_aligned_action(
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


def _delete_pine_kv(sym: str) -> bool:
    """Delete ``pine.signal.{SYM}``; return True when a key was removed."""
    key_sym = str(sym or "").strip().upper()
    if not key_sym:
        return False
    try:
        from options_portal_persistence.kv_store import KvStore

        kv = KvStore()
        if not kv.available:
            return False
        return bool(kv.delete(f"pine.signal.{key_sym}"))
    except Exception:  # noqa: BLE001
        logger.exception("delete pine.signal.%s failed", key_sym)
        return False


def apply_candle_miss_fallback(row: dict[str, Any]) -> dict[str, Any]:
    """When GET has no real Upstox candles — do not leave stale KV Action/Trend.

    Deletes ``pine.signal.*``, applies pivot/signal feature gates, and forces
    Trend **NEUTRAL** when buy/sellsignal evidence is missing or bar_ts is stale
    (TV may already show NEUTRAL while sticky BULLISH lingered from synthetic).
    """
    sym = str(row.get("symbol") or "").strip().upper()
    if sym:
        _delete_pine_kv(sym)
    feats = dict(row.get("features") or {})
    # Refuse synthetic-sourced sticky (worker hash bars).
    src = str(
        row.get("candle_source") or feats.get("candle_source") or ""
    ).strip().lower()
    if src and src != "upstox":
        feats.pop("last_confirmed_pivot_is_high", None)
        feats.pop("lastConfirmedPivotIsHigh", None)
        # Keep signal only if it came with upstox; synthetic → wipe sticky bias.
        if src == "synthetic":
            feats.pop("signal", None)
            feats.pop("buysignal", None)
            feats.pop("sellsignal", None)

    before = _norm_action(row.get("action") or row.get("pine_action"))
    action = enforce_pivot_aligned_action(before, row.get("trend"), features=feats)
    pivot = _feature_pivot(feats)
    source = "pivot" if pivot is not None else "stale_cleared"
    if pivot is None:
        try:
            sig = int(feats["signal"]) if feats.get("signal") is not None else None
        except (TypeError, ValueError):
            sig = None
        if sig is not None and sig < 0:
            action = "SELL"
            source = "signal"
        elif sig is not None and sig > 0 and src == "upstox":
            action = "BUY"
            source = "signal"
        else:
            # Unverified / synthetic sticky — curated seed (BAJFINANCE=SELL)
            # or refuse unverified BUY (cannot prove pivot low without candles).
            seed_action = None
            try:
                from app.mock_data import MOCK_SIGNALS

                for seed in MOCK_SIGNALS:
                    if str(seed.get("symbol") or "").upper() == sym:
                        seed_action = _norm_action(seed.get("action"))
                        break
            except Exception:  # noqa: BLE001
                seed_action = None
            if seed_action in {"BUY", "SELL"}:
                action = seed_action
                source = "seed_fallback"
            elif before == "BUY":
                action = "SELL"
                source = "refuse_unverified_buy"

    # Trend: without fresh candles never trust sticky BULLISH/BEARISH alone.
    bar_ok = pine_signal_bar_is_current(row.get("bar_ts"))
    try:
        bs = int(feats.get("buysignal") or 0)
    except (TypeError, ValueError):
        bs = 0
    try:
        ss = int(feats.get("sellsignal") or 0)
    except (TypeError, ValueError):
        ss = 0
    if bar_ok and src == "upstox":
        if bs == 1 and ss != 1:
            trend = "BULLISH"
        elif ss == 1 and bs != 1:
            trend = "BEARISH"
        else:
            trend = "NEUTRAL"
    else:
        # Stale bar or unverified source → match TV-style NEUTRAL until recompute.
        trend = "NEUTRAL"

    action = enforce_pivot_aligned_action(action, trend, features=feats)
    if pivot is True:
        action = "SELL"
        source = "pivot"
    elif pivot is False:
        action = "BUY"
        source = "pivot"
    row["action"] = action
    row["pine_action"] = action
    row["display_action"] = action
    row["trend"] = trend
    row["features"] = {
        **feats,
        "trend": trend,
        "pine_recompute_failed": True,
    }
    row["action_source"] = source
    row["pine_recompute_failed"] = True
    row["pine_persisted"] = False
    row["candle_source"] = src or "unavailable"
    row["updated_at"] = datetime.now(tz=timezone.utc).isoformat()
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
        feats = {
            **dict(row.get("features") or {}),
            "action_no_hold": True,
            "signal_engine": "reversal_pro_v32",
            "pine": True,
            "candle_source": "upstox",
        }
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
                "features": feats,
                "bar_ts": row.get("bar_ts"),
                "updated_at": row.get("updated_at")
                or datetime.now(tz=timezone.utc).isoformat(),
                "timeframe": str(row.get("timeframe") or "15m"),
                "signal_engine": "reversal_pro_v32",
                "candle_source": "upstox",
            },
        )
    except Exception:  # noqa: BLE001
        logger.exception("persist recomputed pine.signal.%s failed", sym)


def enforce_bearish_pivot_invariant(rows: Sequence[dict[str, Any]]) -> int:
    """Screenshot guarantee: Action follows last confirmed pivot.

    No BUY while ``lastConfirmedPivotIsHigh``; no SELL while pivot low.
    Mutates ``action`` / ``pine_action`` / ``display_action`` in place.
    Returns how many rows were forced.
    """
    fixed = 0
    for row in rows:
        trend = _norm_trend(row.get("trend"))
        feats = dict(row.get("features") or {})
        before = _norm_action(row.get("action") or row.get("pine_action"))
        after = enforce_pivot_aligned_action(before, trend, features=feats)
        if after != before:
            row["action"] = after
            row["pine_action"] = after
            row["display_action"] = after
            row["action_source"] = "pivot"
            if after == "SELL":
                row["invariant_forced_sell"] = True
            else:
                row["invariant_forced_buy"] = True
            fixed += 1
        elif after == "SELL":
            # Keep pine_action aligned even when action already SELL.
            row["pine_action"] = "SELL"
            if row.get("display_action") == "BUY" and not row.get(
                "action_from_open_trade"
            ):
                row["display_action"] = "SELL"
                row["action"] = "SELL"
        elif after == "BUY":
            row["pine_action"] = "BUY"
            if row.get("display_action") == "SELL" and not row.get(
                "action_from_open_trade"
            ):
                row["display_action"] = "BUY"
                row["action"] = "BUY"
    return fixed


async def refresh_rows_from_candles(
    rows: list[dict[str, Any]],
    *,
    cfg: Mapping[str, Any] | None = None,
    force_all: bool = False,
) -> dict[str, Any]:
    """Recompute Pine Action/Trend from live 15m candles when needed.

    Runs on GET /signals so after-hours (worker idle) still updates VEDL.
    When Upstox candles are missing, **clears stale KV** and applies the
    pivot/signal feature gate (never leaves synthetic BUY+BULLISH).
    """
    if not rows:
        return {
            "recomputed": [],
            "attempted": [],
            "candle_symbols": 0,
            "candle_miss_cleared": [],
        }
    need: list[str] = []
    for row in rows:
        sym = str(row.get("symbol") or "").strip().upper()
        if not sym:
            continue
        if force_all or row_needs_candle_recompute(row):
            need.append(sym)
    need = sorted(set(need))
    if not need:
        return {
            "recomputed": [],
            "attempted": [],
            "candle_symbols": 0,
            "candle_miss_cleared": [],
        }

    from app.marks import _live_15m_candles_map

    candles_by_sym = await _live_15m_candles_map(need)
    params = pine_params_from_config(cfg)
    recomputed: list[str] = []
    cleared: list[str] = []
    for row in rows:
        sym = str(row.get("symbol") or "").strip().upper()
        if sym not in need:
            continue
        candles = candles_by_sym.get(sym) or []
        if not candles:
            apply_candle_miss_fallback(row)
            cleared.append(sym)
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
            apply_candle_miss_fallback(row)
            cleared.append(sym)
    return {
        "recomputed": recomputed,
        "attempted": need,
        "candle_symbols": len(candles_by_sym),
        "candle_miss_cleared": cleared,
    }
