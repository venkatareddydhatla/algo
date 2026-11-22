"""In-memory mock payloads so the UI runs without a full DB.

Dry-run watchlist Action is Pine-only (BUY / SELL / HOLD). Worker-persisted
``pine.signal.*`` KV rows override synthetics when present.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from options_portal_domain.pine_signals import (
    TREND_BEARISH,
    TREND_BULLISH,
    TREND_NEUTRAL,
    reconcile_persisted_action,
    watchlist_pine_action,
)
from options_portal_domain.universe import INDEX_SYMBOLS
from options_portal_hybrid.explain import attach_reason_fields  # noqa: F401 — tests/legacy

NOW = datetime.now(tz=timezone.utc).isoformat()


def _normalize_trend(trend: str | None) -> str:
    """Pine info-table Trend: BULLISH / BEARISH / NEUTRAL."""
    t = str(trend or TREND_NEUTRAL).strip().upper()
    if t in {TREND_BULLISH, "BULL", "UP"}:
        return TREND_BULLISH
    if t in {TREND_BEARISH, "BEAR", "DOWN"}:
        return TREND_BEARISH
    return TREND_NEUTRAL

# Hand-tuned showcase rows for curated TradingView equities only (no indices /
# leftover NIFTY·BANKNIFTY·TCS·RELIANCE). Seeds never append outside the
# requested watchlist — see build_universe_signals.
_SEED_SIGNALS: list[dict[str, Any]] = [
    {
        "id": "sig-bajfinance-1",
        "symbol": "BAJFINANCE",
        "timeframe": "15m",
        "action": "SELL",
        "trend": TREND_BEARISH,
        "hybrid_score": -1.0,
        "tech_score": -1.0,
        "news_score": 0.0,
        "confidence": 0.85,
        "expiry_hint": "monthly",
        "updated_at": NOW,
        "reasons": [
            "D1 bearish reversal flip",
            "STRONG bearish (D1 + sellsignal)",
            "sell_signal=True",
        ],
        "reason_kind": "pine",
        "reason_summary": "STRONG bearish (D1 + EMA trend)",
        "reason_detail": (
            "Technical:\n- D1 bearish reversal flip\n"
            "- STRONG bearish (D1 + sellsignal)\n- sell_signal=True"
        ),
        "signal_engine": "reversal_pro_v32",
    },
    {
        "id": "sig-bhartiartl-1",
        "symbol": "BHARTIARTL",
        "timeframe": "15m",
        "action": "BUY",
        "trend": TREND_BULLISH,
        "hybrid_score": 1.0,
        "tech_score": 1.0,
        "news_score": 0.0,
        "confidence": 0.85,
        "expiry_hint": "monthly",
        "updated_at": NOW,
        "reasons": [
            "U1 bullish reversal flip",
            "STRONG bullish (U1 + buysignal)",
            "buy_signal=True",
        ],
        "reason_kind": "pine",
        "reason_summary": "STRONG bullish (U1 + EMA trend)",
        "reason_detail": (
            "Technical:\n- U1 bullish reversal flip\n"
            "- STRONG bullish (U1 + buysignal)\n- buy_signal=True"
        ),
        "signal_engine": "reversal_pro_v32",
    },
    {
        "id": "sig-vedl-1",
        "symbol": "VEDL",
        "timeframe": "15m",
        "action": "BUY",
        "trend": TREND_NEUTRAL,
        "hybrid_score": 0.25,
        "tech_score": 0.25,
        "news_score": 0.0,
        "confidence": 0.3,
        "expiry_hint": "monthly",
        "updated_at": NOW,
        "reasons": [
            "no fresh U1/D1 — Action BUY via neutral · Trend=NEUTRAL"
        ],
        "reason_kind": "pine",
        "reason_summary": "neutral fallback → BUY",
        "reason_detail": (
            "Technical:\n- no fresh U1/D1 — Action BUY via neutral · Trend=NEUTRAL"
        ),
        "signal_engine": "reversal_pro_v32",
        "action_source": "neutral",
    },
]

MOCK_SIGNALS: list[dict[str, Any]] = [
    # Keep Pine reason_* as authored (do not reclassify via hybrid explain).
    dict(row) for row in _SEED_SIGNALS
]

MOCK_NEWS: list[dict[str, Any]] = [
    {
        "id": "news-1",
        "published_at": NOW,
        "score": -0.72,
        "label": "bearish",
        "symbols": ["RELIANCE"],
        "title": "Broker cuts target on Reliance amid margin pressure",
        "source": "livemint",
    },
    {
        "id": "news-2",
        "published_at": NOW,
        "score": 0.41,
        "label": "bullish",
        "symbols": ["NIFTY", "BANKNIFTY"],
        "title": "Foreign inflows lift benchmark indices into close",
        "source": "economic_times",
    },
    {
        "id": "news-3",
        "published_at": NOW,
        "score": 0.05,
        "label": "neutral",
        "symbols": ["TCS"],
        "title": "Quiet session ahead for IT majors",
        "source": "moneycontrol",
    },
]

MOCK_TRADES: dict[str, Any] = {
    "mode": "dry_run",
    "positions": [
        {
            "symbol": "NIFTY 24500 CE",
            "side": "long",
            "qty": 25,
            "avg_price": 138.0,
            "unrealized_pnl": 1125.0,
            "opened_at": "2026-11-15T04:05:00+00:00",
            "entry_ts": "2026-11-15T04:05:00+00:00",
            "opened_at_ist_display": "2026-11-15 09:35 IST",
            "entry_ts_ist_display": "2026-11-15 09:35 IST",
        },
        {
            "symbol": "RELIANCE 2920 PE",
            "side": "short",
            "qty": 250,
            "avg_price": 64.5,
            "unrealized_pnl": -2400.0,
            "opened_at": "2026-11-14T05:00:00+00:00",
            "entry_ts": "2026-11-14T05:00:00+00:00",
            "opened_at_ist_display": "2026-11-14 10:30 IST",
            "entry_ts_ist_display": "2026-11-14 10:30 IST",
        },
    ],
    "fills": [
        {
            "ts": NOW,
            "status": "SIM",
            "side": "BUY",
            "symbol": "NIFTY 24500 CE",
            "qty": 25,
            "price": 142.5,
            "signal_id": "sig-nifty-1",
        }
    ],
    "day_realized": 3100.0,
    "day_unrealized": -1275.0,
    "fees_est": 180.0,
}

MOCK_CONFIG: dict[str, Any] = {
    "trading": {
        "mode": "dry_run",
        "halt": False,
        "symbols": ["NIFTY", "BANKNIFTY", "RELIANCE", "TCS"],
        "timeframe": "15m",
        "max_active_symbols": 0,
        "scan_batch_size": 8,
        "note": "Live mode switch is UI-present but placement remains gated in v1",
    },
    "hybrid": {
        "weights": {"tech": 0.65, "news": 0.35},
        "enter_long": 0.45,
        "enter_short": -0.45,
        "news_veto_abs": 0.70,
    },
}


def _stable_unit(symbol: str, salt: str = "") -> float:
    """Deterministic [0, 1) float from symbol (stable across runs)."""
    digest = hashlib.sha256(f"{symbol}|{salt}".encode("ascii")).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


def _aggregate_live_news(live_rows: list[dict[str, Any]]) -> tuple[float, float, list[str]]:
    """Return (news_score, news_confidence, headline snippets) from live rows."""
    if not live_rows:
        return 0.0, 0.0, []
    weighted = 0.0
    conf_sum = 0.0
    headlines: list[str] = []
    for row in live_rows:
        score = float(row.get("score") or 0.0)
        conf = float(row.get("confidence") or (min(1.0, abs(score) + 0.3)))
        weighted += score * conf
        conf_sum += conf
        title = (row.get("title") or "").strip()
        if title:
            headlines.append(title[:120])
    news_score = weighted / max(conf_sum, 1e-9)
    news_conf = conf_sum / len(live_rows)
    return max(-1.0, min(1.0, round(news_score, 4))), round(news_conf, 4), headlines[:3]


def _normalize_pine_action(action: str | None) -> str:
    """Map legacy CE/PE labels onto Pine BUY/SELL/HOLD."""
    act = str(action or "HOLD").upper()
    if act in {"BUY", "BUY_CE_BIAS", "BUY_CE", "LONG"}:
        return "BUY"
    if act in {"SELL", "BUY_PE_BIAS", "BUY_PE", "SHORT"}:
        return "SELL"
    return "HOLD"


def synthetic_signal_for_symbol(
    symbol: str,
    *,
    timeframe: str = "15m",
    updated_at: str | None = None,
    enter_long: float = 0.45,
    enter_short: float = -0.45,
    live_news: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Placeholder Pine-shaped Action until the worker persists real signals.

    Action is BUY / SELL / HOLD only (no hybrid/news drivers). ``live_news`` is
    ignored for Action (kept for API compatibility).
    """
    del enter_long, enter_short, live_news  # Pine Action ignores hybrid/news
    sym = symbol.upper()
    if sym in INDEX_SYMBOLS:
        reasons = ["index_not_equity — Pine stocks-only → HOLD"]
        return {
            "id": f"pine-{sym.lower()}",
            "symbol": sym,
            "timeframe": timeframe,
            "action": "HOLD",
            "trend": TREND_NEUTRAL,
            "hybrid_score": 0.0,
            "tech_score": 0.0,
            "news_score": 0.0,
            "confidence": 0.2,
            "expiry_hint": "weekly",
            "updated_at": updated_at or NOW,
            "reasons": reasons,
            "stub": False,
            "dry_run_synthetic": True,
            "live_news": False,
            "signal_engine": "reversal_pro_v32",
            "reason_kind": "pine",
            "reason_summary": "index blocked",
            "reason_detail": "Technical:\n- " + reasons[0],
        }

    # Deterministic placeholder until worker KV: Action BUY/SELL only + Trend.
    roll = _stable_unit(sym, "pine_action")
    trend_roll = _stable_unit(sym, "pine_trend")
    reasons: list[str] = [
        "Reversal Pro v3.2 placeholder (await worker candles)",
    ]
    if trend_roll >= 0.55:
        trend = TREND_BULLISH
    elif trend_roll <= 0.35:
        trend = TREND_BEARISH
    else:
        trend = TREND_NEUTRAL
    if roll >= 0.88:
        action = "BUY"
        score = 1.0
        reasons.extend(
            [
                "U1 bullish reversal flip",
                "STRONG bullish (U1 + buysignal)",
                "buy_signal=True (placeholder until worker persist)",
                f"trend={trend}",
            ]
        )
        summary = "STRONG bullish (U1 + EMA trend)"
    elif roll <= 0.12:
        action = "SELL"
        score = -1.0
        reasons.extend(
            [
                "D1 bearish reversal flip",
                "STRONG bearish (D1 + sellsignal)",
                "sell_signal=True (placeholder until worker persist)",
                f"trend={trend}",
            ]
        )
        summary = "STRONG bearish (D1 + EMA trend)"
    else:
        # No HOLD on equities: sticky/neutral Action; Trend still shown.
        action = "BUY" if trend == TREND_BULLISH else (
            "SELL" if trend == TREND_BEARISH else "BUY"
        )
        score = 0.5 if action == "BUY" else -0.5
        reasons.append(
            f"no fresh U1/D1 — Action {action} via sticky/trend · Trend={trend}"
        )
        summary = f"no entry · Trend {trend}"

    detail = "Technical:\n" + "\n".join(f"- {r}" for r in reasons)
    return {
        "id": f"pine-{sym.lower()}",
        "symbol": sym,
        "timeframe": timeframe,
        "action": action,
        "trend": trend,
        "hybrid_score": score,
        "tech_score": score,
        "news_score": 0.0,
        "confidence": 0.85 if roll >= 0.88 or roll <= 0.12 else 0.4,
        "expiry_hint": "monthly",
        "updated_at": updated_at or NOW,
        "reasons": reasons,
        "stub": False,
        "dry_run_synthetic": True,
        "live_news": False,
        "signal_engine": "reversal_pro_v32",
        "reason_kind": "pine",
        "reason_summary": summary,
        "reason_detail": detail,
    }


# Pre-Reversal-Pro hybrid / confluence payloads (INFY-style 2026-11-20 linger).
_LEGACY_HYBRID_ACTIONS = frozenset(
    {"BUY_CE_BIAS", "BUY_PE_BIAS", "BUY_CE", "BUY_PE"}
)
_LEGACY_HYBRID_ENGINES = frozenset(
    {"hybrid_confluence", "hybrid", "confluence", "weighted_hybrid"}
)
_LEGACY_HYBRID_REASON_KINDS = frozenset(
    {"hybrid", "technical", "news", "neutral"}
)


def is_stale_hybrid_signal_kv(payload: dict[str, Any] | None) -> bool:
    """True when ``pine.signal.*`` is pre-Reversal-Pro hybrid junk — ignore/delete.

    Customs (e.g. INFY) often still have 2026-11-20 HOLD + hybrid_score rows in
    SQLite. Those must not win over the live 15m Reversal Pro path.
    """
    if not isinstance(payload, dict):
        return False
    feats = dict(payload.get("features") or {})
    engine = str(
        payload.get("signal_engine") or feats.get("signal_engine") or ""
    ).lower()
    if engine == "reversal_pro_v32" or feats.get("pine") is True:
        return False
    if feats.get("action_no_hold") is True:
        return False
    if engine in _LEGACY_HYBRID_ENGINES:
        return True
    reason = str(payload.get("reason_kind") or "").lower()
    if reason in _LEGACY_HYBRID_REASON_KINDS:
        return True
    act = str(payload.get("action") or "").upper()
    if act in _LEGACY_HYBRID_ACTIONS:
        return True
    # HOLD without Reversal Pro markers — hybrid-era linger (no Action/Trend).
    if act == "HOLD" and not engine:
        return True
    return False


def _payload_action_no_hold(payload: dict[str, Any]) -> bool:
    """Reversal Pro: watchlist Action stays BUY/SELL (no expire→HOLD)."""
    feats = dict(payload.get("features") or {})
    if "action_no_hold" in feats:
        return bool(feats.get("action_no_hold"))
    engine = str(
        payload.get("signal_engine") or feats.get("signal_engine") or ""
    ).lower()
    return engine == "reversal_pro_v32"


def pine_row_from_kv(
    payload: dict[str, Any],
    *,
    timeframe: str = "15m",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Normalize a worker-persisted ``pine.signal.*`` KV payload into an API row.

    With ``action_no_hold`` (Reversal Pro default), BUY/SELL do not expire to HOLD.
    """
    sym = str(payload.get("symbol") or "").upper()
    clock = now or datetime.now(tz=timezone.utc)
    feats_early = dict(payload.get("features") or {})
    trend_early = _normalize_trend(
        payload.get("trend") or feats_early.get("trend")
    )
    # Stale U1 sticky BUY while pivot-high / D1 path + BEARISH → SELL (VEDL).
    raw_action = reconcile_persisted_action(
        str(payload.get("action") or "HOLD"),
        trend_early,
        features=feats_early,
    )
    raw_action = _normalize_pine_action(raw_action)
    bar_ts = payload.get("bar_ts")
    no_hold = _payload_action_no_hold(payload)
    action = watchlist_pine_action(
        raw_action,
        bar_ts,
        now=clock,
        timeframe_minutes=15,
        expire_to_hold=not no_hold,
    )
    expired = raw_action in {"BUY", "SELL"} and action == "HOLD"
    if expired:
        score = 0.0
        confidence = 0.0
        reasons = list(payload.get("reasons") or [])
        reasons = [f"stale Pine {raw_action} expired → HOLD", *reasons[:6]]
        summary = "stale Pine signal → HOLD"
        detail = (
            "Neutral: signal bar no longer current; watchlist Action expired to HOLD"
            + (f" (bar_ts={bar_ts})" if bar_ts else " (missing bar_ts)")
        )
    else:
        score = float(
            payload.get("score")
            or (1.0 if action == "BUY" else -1.0 if action == "SELL" else 0.0)
        )
        confidence = float(
            payload.get("confidence") or (0.85 if action != "HOLD" else 0.3)
        )
        reasons = list(payload.get("reasons") or [])
        summary = str(
            payload.get("reason_summary")
            or (
                "STRONG bullish (U1 + EMA trend)"
                if action == "BUY"
                else "STRONG bearish (D1 + EMA trend)"
                if action == "SELL"
                else "no Reversal Pro signal"
            )
        )
        detail = str(
            payload.get("reason_detail")
            or (
                "Technical:\n" + "\n".join(f"- {r}" for r in reasons)
                if reasons
                else "Neutral: no U1/D1"
            )
        )
    feats = dict(payload.get("features") or {})
    trend = _normalize_trend(
        payload.get("trend") or feats.get("trend")
    )
    return {
        "id": f"pine-{sym.lower()}",
        "symbol": sym,
        "timeframe": str(payload.get("timeframe") or timeframe),
        "action": action,
        "trend": trend,
        "hybrid_score": score,
        "tech_score": score,
        "news_score": 0.0,
        "confidence": confidence,
        "expiry_hint": "weekly" if sym in INDEX_SYMBOLS else "monthly",
        "updated_at": str(payload.get("updated_at") or NOW),
        "reasons": reasons,
        "stub": False,
        "dry_run_synthetic": False,
        "live_news": False,
        "signal_engine": "reversal_pro_v32",
        "pine_persisted": True,
        "pine_action_expired": expired,
        "bar_ts": bar_ts,
        "reason_kind": "pine",
        "reason_summary": summary,
        "reason_detail": detail,
        "features": feats,
        "action_source": payload.get("action_source") or feats.get("action_source"),
    }


def _expire_persisted_pine_payload(
    kv: Any,
    key: str,
    payload: dict[str, Any],
    *,
    now: datetime,
) -> dict[str, Any]:
    """Rewrite stale BUY/SELL KV rows to HOLD (APOLLOHOSP-style linger fix)."""
    updated = dict(payload)
    updated.update(
        {
            "action": "HOLD",
            "buy_signal": False,
            "sell_signal": False,
            "score": 0.0,
            "confidence": 0.0,
            "reason_summary": "stale Pine signal → HOLD",
            "reason_detail": (
                "Neutral: signal bar no longer current; "
                "watchlist Action expired to HOLD"
                + (
                    f" (bar_ts={payload.get('bar_ts')})"
                    if payload.get("bar_ts")
                    else " (missing bar_ts)"
                )
            ),
            "updated_at": now.isoformat(),
            "expired_from": _normalize_pine_action(str(payload.get("action") or "")),
        }
    )
    reasons = list(payload.get("reasons") or [])
    updated["reasons"] = [
        f"stale Pine {updated['expired_from']} expired → HOLD",
        *reasons[:6],
    ]
    try:
        kv.set(f"pine.signal.{key}", updated)
    except Exception:  # noqa: BLE001
        pass
    return updated


def load_persisted_pine_signals(
    symbols: list[str],
    *,
    now: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    """Read worker-persisted Pine actions from KvStore when available.

    Stale BUY/SELL (signal bar not current / same 15m, or missing ``bar_ts``)
    are rewritten to HOLD in KV and returned as HOLD.
    """
    out: dict[str, dict[str, Any]] = {}
    try:
        from options_portal_persistence.kv_store import KvStore

        kv = KvStore()
        if not kv.available:
            return out
        clock = now or datetime.now(tz=timezone.utc)
        for sym in symbols:
            key = str(sym).upper()
            payload = kv.get(f"pine.signal.{key}")
            if not (isinstance(payload, dict) and payload.get("action")):
                continue
            # Drop pre-Reversal-Pro hybrid KV (customs like INFY HOLD + old ts).
            if is_stale_hybrid_signal_kv(payload):
                try:
                    kv.delete(f"pine.signal.{key}")
                except Exception:  # noqa: BLE001
                    pass
                continue
            feats = dict(payload.get("features") or {})
            trend = _normalize_trend(payload.get("trend") or feats.get("trend"))
            reconciled = reconcile_persisted_action(
                str(payload.get("action") or "HOLD"),
                trend,
                features=feats,
            )
            raw = _normalize_pine_action(reconciled)
            # Refresh stale KV Action in place when pivot/D1 path disagrees.
            if raw != _normalize_pine_action(str(payload.get("action") or "")):
                try:
                    updated = dict(payload)
                    updated["action"] = raw
                    updated["score"] = (
                        1.0 if raw == "BUY" else -1.0 if raw == "SELL" else 0.0
                    )
                    updated["action_source"] = "pivot"
                    updated["updated_at"] = clock.isoformat()
                    updated["reason_summary"] = (
                        "last pivot high (D1 path) → SELL"
                        if raw == "SELL"
                        else "last pivot low (U1 path) → BUY"
                    )
                    kv.set(f"pine.signal.{key}", updated)
                    payload = updated
                except Exception:  # noqa: BLE001
                    payload = {**payload, "action": raw}
            no_hold = _payload_action_no_hold(payload)
            live = watchlist_pine_action(
                raw,
                payload.get("bar_ts"),
                now=clock,
                timeframe_minutes=15,
                expire_to_hold=not no_hold,
            )
            if (not no_hold) and raw in {"BUY", "SELL"} and live == "HOLD":
                # Expire → delete (do not rewrite HOLD; synthetics / candle
                # recompute must supply Reversal Pro BUY/SELL + Trend).
                try:
                    kv.delete(f"pine.signal.{key}")
                except Exception:  # noqa: BLE001
                    pass
                continue
            row = pine_row_from_kv(payload, now=clock)
            out[key] = row
    except Exception:  # noqa: BLE001
        return out
    return out


def clear_persisted_pine_signals(symbols: list[str]) -> list[str]:
    """Delete ``pine.signal.*`` KV rows so sub-floor names cannot linger on watchlist."""
    cleared: list[str] = []
    try:
        from options_portal_persistence.kv_store import KvStore

        kv = KvStore()
        if not kv.available:
            return cleared
        for sym in symbols:
            key = str(sym).strip().upper()
            if not key:
                continue
            if kv.delete(f"pine.signal.{key}"):
                cleared.append(key)
    except Exception:  # noqa: BLE001
        return cleared
    return cleared


def clear_stale_hybrid_signal_kv(symbols: list[str] | None = None) -> list[str]:
    """Delete hybrid-era ``pine.signal.*`` rows (optional symbol filter).

    Used when adding customs and by docs/ops so INFY-style HOLD + old timestamp
    cannot block Reversal Pro Action/Trend.
    """
    cleared: list[str] = []
    try:
        from options_portal_persistence.kv_store import KvStore

        kv = KvStore()
        if not kv.available:
            return cleared
        keys: list[str]
        if symbols:
            keys = [f"pine.signal.{str(s).strip().upper()}" for s in symbols if s]
        else:
            # Best-effort: only known watchlist keys when no filter.
            try:
                from options_portal_domain.config import watchlist_symbols

                keys = [
                    f"pine.signal.{str(s).strip().upper()}"
                    for s in watchlist_symbols()
                    if s
                ]
            except Exception:  # noqa: BLE001
                return cleared
        for key in keys:
            sym = key.rsplit(".", 1)[-1]
            payload = kv.get(key)
            if is_stale_hybrid_signal_kv(
                payload if isinstance(payload, dict) else None
            ):
                if kv.delete(key):
                    cleared.append(sym)
    except Exception:  # noqa: BLE001
        return cleared
    return cleared


def display_action_for_open_side(side: str | None) -> str | None:
    """Map open paper side → sticky watchlist Action (display / mode only).

    LONG → BUY, SHORT → SELL. Not an entry signal — entries still require a
    fresh Pine signal bar in the worker.
    """
    s = str(side or "").strip().lower()
    if s in {"long", "ce"}:
        return "BUY"
    if s in {"short", "pe"}:
        return "SELL"
    return None


def load_open_trade_display_modes(
    *,
    mode: str | None = None,
) -> dict[str, str]:
    """Open paper positions → ``{SYMBOL: BUY|SELL}`` for watchlist Action overlay.

    Only symbols with an **actually open** paper row (qty > 0 + long/short side).
    Empty when flat / store unavailable — never invent overlay while flat.
    """
    out: dict[str, str] = {}
    try:
        from options_portal_domain.config import trading_mode
        from options_portal_persistence.paper_trades import PaperTradeStore

        requested = (mode or trading_mode() or "dry_run").strip().lower()
        safe_mode = "dry_run" if requested == "live" else requested
        store = PaperTradeStore()
        if not store.available:
            return out
        for pos in store.list_positions(mode=safe_mode):  # type: ignore[arg-type]
            sym = str(pos.get("symbol") or "").strip().upper()
            try:
                qty = int(pos.get("qty") or 0)
            except (TypeError, ValueError):
                qty = 0
            if qty <= 0:
                continue  # not an open position — no overlay while flat
            sticky = display_action_for_open_side(pos.get("side"))
            if sym and sticky:
                out[sym] = sticky
    except Exception:  # noqa: BLE001
        return out
    return out


def apply_open_trade_display_actions(
    rows: list[dict[str, Any]],
    open_modes: dict[str, str] | None = None,
    *,
    load_if_missing: bool = True,
) -> list[dict[str, Any]]:
    """Overlay Action from open paper side only when it does not hide a fresher U1/D1.

    - ``pine_action``: Reversal Pro Action (most recent confirmed U1/D1 sticky).
    - ``action`` / ``display_action``: while a paper position is **actually open**,
      overlay BUY (long) / SELL (short) **only** when pine is HOLD or same-side.
      A fresher opposite pine Action (e.g. D1→SELL while long still open) wins —
      Action must track the latest green/red Reversal, not the entry sticky.
    - Flat / qty≤0 → no overlay (Action = pine).
    - ``trend`` is **never** overwritten by the open-trade overlay.
    - Overlay is **display / mode only** — not a fresh entry signal.
    """
    modes = open_modes
    if modes is None and load_if_missing:
        modes = load_open_trade_display_modes()
    modes = modes or {}
    for row in rows:
        pine = _normalize_pine_action(
            str(row.get("pine_action") if "pine_action" in row else row.get("action"))
        )
        row["pine_action"] = pine
        row["trend"] = _normalize_trend(row.get("trend"))
        sym = str(row.get("symbol") or "").strip().upper()
        sticky = modes.get(sym)
        # Overlay only for truly open symbols, and never over a fresher opposite
        # pine U1/D1 (VEDL: open-long sticky BUY must not hide later D1 SELL).
        if sticky in {"BUY", "SELL"} and pine in {"HOLD", sticky}:
            row["action"] = sticky
            row["display_action"] = sticky
            row["open_trade_mode"] = sticky
            row["action_from_open_trade"] = True
        else:
            row["action"] = pine
            row["display_action"] = pine
            row["open_trade_mode"] = sticky if sticky in {"BUY", "SELL"} else None
            row["action_from_open_trade"] = False
    return rows


def rows_for_top_strip(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """TOP BUY/SELL strip uses fresh ``pine_action`` — not sticky open-trade Action."""
    out: list[dict[str, Any]] = []
    for row in rows:
        r = dict(row)
        pine = _normalize_pine_action(
            str(r.get("pine_action") if "pine_action" in r else r.get("action"))
        )
        r["action"] = pine
        r["pine_action"] = pine
        out.append(r)
    return out


def build_universe_signals(
    symbols: list[str],
    *,
    timeframe: str = "15m",
    seed_rows: list[dict[str, Any]] | None = None,
    live_news_by_symbol: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Watchlist rows: prefer worker Pine KV, else Pine-shaped synthetics."""
    del live_news_by_symbol  # Action is Pine-only; news no longer drives Action
    seeds = {
        r["symbol"].upper(): dict(r)
        for r in (seed_rows or MOCK_SIGNALS)
    }
    persisted = load_persisted_pine_signals([str(s).upper() for s in symbols])
    now = datetime.now(tz=timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sym in symbols:
        key = sym.upper()
        if key in seen:
            continue
        seen.add(key)
        if key in persisted:
            row = dict(persisted[key])
            row["updated_at"] = row.get("updated_at") or now
            rows.append(row)
        elif key in seeds:
            row = dict(seeds[key])
            row["action"] = _normalize_pine_action(row.get("action"))
            row["updated_at"] = now
            row.setdefault("signal_engine", "reversal_pro_v32")
            row.setdefault("reason_kind", "pine")
            rows.append(row)
        else:
            rows.append(
                synthetic_signal_for_symbol(
                    key,
                    timeframe=timeframe,
                    updated_at=now,
                )
            )
    # Do not append leftover seed symbols (old NIFTY/BANKNIFTY/TCS, etc.)
    # that are outside the requested curated watchlist + customs.
    return rows
