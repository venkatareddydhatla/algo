"""In-memory mock payloads so the UI runs without a full DB.

Dry-run watchlist Action is Pine-only (BUY / SELL / HOLD). Worker-persisted
``pine.signal.*`` KV rows override synthetics when present.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from options_portal_domain.universe import INDEX_SYMBOLS
from options_portal_hybrid.explain import attach_reason_fields  # noqa: F401 — tests/legacy

NOW = datetime.now(tz=timezone.utc).isoformat()

# Hand-tuned showcase rows (Pine Action labels only).
_SEED_SIGNALS: list[dict[str, Any]] = [
    {
        "id": "sig-nifty-1",
        "symbol": "NIFTY",
        "timeframe": "15m",
        "action": "HOLD",
        "hybrid_score": 0.0,
        "tech_score": 0.0,
        "news_score": 0.0,
        "confidence": 0.2,
        "expiry_hint": "weekly",
        "updated_at": NOW,
        "reasons": [
            "index_not_equity — Pine stocks-only watchlist Action=HOLD",
        ],
        "reason_kind": "pine",
        "reason_summary": "index blocked",
        "reason_detail": "Technical:\n- index_not_equity",
    },
    {
        "id": "sig-bn-1",
        "symbol": "BANKNIFTY",
        "timeframe": "15m",
        "action": "HOLD",
        "hybrid_score": 0.0,
        "tech_score": 0.0,
        "news_score": 0.0,
        "confidence": 0.2,
        "expiry_hint": "weekly",
        "updated_at": NOW,
        "reasons": ["index_not_equity — Pine stocks-only"],
        "reason_kind": "pine",
        "reason_summary": "index blocked",
        "reason_detail": "Technical:\n- index_not_equity",
    },
    {
        "id": "sig-rel-1",
        "symbol": "RELIANCE",
        "timeframe": "15m",
        "action": "SELL",
        "hybrid_score": -1.0,
        "tech_score": -1.0,
        "news_score": 0.0,
        "confidence": 0.85,
        "expiry_hint": "monthly",
        "updated_at": NOW,
        "reasons": [
            "EMA13 crossunder EMA21",
            "ADX(14)=24.50 ok",
            "vol/sma20=1.35 ok",
            "sell_signal=True",
        ],
        "reason_kind": "pine",
        "reason_summary": "EMA crossunder+ADX+vol",
        "reason_detail": (
            "Technical:\n- EMA13 crossunder EMA21\n- ADX(14)=24.50 ok\n"
            "- vol/sma20=1.35 ok\n- sell_signal=True"
        ),
    },
    {
        "id": "sig-tcs-1",
        "symbol": "TCS",
        "timeframe": "15m",
        "action": "HOLD",
        "hybrid_score": 0.0,
        "tech_score": 0.0,
        "news_score": 0.0,
        "confidence": 0.3,
        "expiry_hint": "monthly",
        "updated_at": NOW,
        "reasons": ["buy_signal=False sell_signal=False → HOLD"],
        "reason_kind": "pine",
        "reason_summary": "no Pine signal",
        "reason_detail": "Technical:\n- buy_signal=False sell_signal=False → HOLD",
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
            "signal_engine": "pine_nifty50_v2",
            "reason_kind": "pine",
            "reason_summary": "index blocked",
            "reason_detail": "Technical:\n- " + reasons[0],
        }

    # Deterministic placeholder: ~12% BUY, ~12% SELL, else HOLD (until worker KV).
    roll = _stable_unit(sym, "pine_action")
    st_bull = _stable_unit(sym, "pine_st") > 0.45
    adx = 15.0 + _stable_unit(sym, "pine_adx") * 25.0
    vol_ratio = 0.8 + _stable_unit(sym, "pine_vol") * 0.8
    adx_ok = adx > 20.0
    high_vol = vol_ratio > 1.2
    reasons: list[str] = [
        f"EMA13/EMA21 placeholder (await worker candles)",
        "Supertrend bullish" if st_bull else "Supertrend bearish",
        f"ADX(14)={adx:.2f} {'ok' if adx_ok else 'need>20'}",
        f"vol/sma20={vol_ratio:.2f} {'ok' if high_vol else 'need>1.2'}",
    ]
    if roll >= 0.88 and st_bull and high_vol and adx_ok:
        action = "BUY"
        score = 1.0
        reasons.append("buy_signal=True (placeholder until worker persist)")
        summary = "EMA cross+ST+ADX+vol"
    elif roll <= 0.12 and high_vol and adx_ok:
        action = "SELL"
        score = -1.0
        reasons.append("sell_signal=True (placeholder until worker persist)")
        summary = "EMA crossunder+ADX+vol"
    else:
        action = "HOLD"
        score = 0.0
        reasons.append("buy_signal=False sell_signal=False → HOLD")
        summary = "no Pine signal"

    detail = "Technical:\n" + "\n".join(f"- {r}" for r in reasons)
    return {
        "id": f"pine-{sym.lower()}",
        "symbol": sym,
        "timeframe": timeframe,
        "action": action,
        "hybrid_score": score,
        "tech_score": score,
        "news_score": 0.0,
        "confidence": 0.85 if action != "HOLD" else 0.3,
        "expiry_hint": "monthly",
        "updated_at": updated_at or NOW,
        "reasons": reasons,
        "stub": False,
        "dry_run_synthetic": True,
        "live_news": False,
        "signal_engine": "pine_nifty50_v2",
        "reason_kind": "pine",
        "reason_summary": summary,
        "reason_detail": detail,
    }


def pine_row_from_kv(payload: dict[str, Any], *, timeframe: str = "15m") -> dict[str, Any]:
    """Normalize a worker-persisted ``pine.signal.*`` KV payload into an API row."""
    sym = str(payload.get("symbol") or "").upper()
    action = _normalize_pine_action(str(payload.get("action") or "HOLD"))
    score = float(payload.get("score") or (1.0 if action == "BUY" else -1.0 if action == "SELL" else 0.0))
    reasons = list(payload.get("reasons") or [])
    summary = str(payload.get("reason_summary") or (
        "EMA cross+ST+ADX+vol" if action == "BUY"
        else "EMA crossunder+ADX+vol" if action == "SELL"
        else "no Pine signal"
    ))
    detail = str(payload.get("reason_detail") or (
        "Technical:\n" + "\n".join(f"- {r}" for r in reasons) if reasons else "Neutral: no Pine buy/sell"
    ))
    return {
        "id": f"pine-{sym.lower()}",
        "symbol": sym,
        "timeframe": str(payload.get("timeframe") or timeframe),
        "action": action,
        "hybrid_score": score,
        "tech_score": score,
        "news_score": 0.0,
        "confidence": float(payload.get("confidence") or (0.85 if action != "HOLD" else 0.3)),
        "expiry_hint": "weekly" if sym in INDEX_SYMBOLS else "monthly",
        "updated_at": str(payload.get("updated_at") or NOW),
        "reasons": reasons,
        "stub": False,
        "dry_run_synthetic": False,
        "live_news": False,
        "signal_engine": "pine_nifty50_v2",
        "pine_persisted": True,
        "reason_kind": "pine",
        "reason_summary": summary,
        "reason_detail": detail,
        "features": dict(payload.get("features") or {}),
    }


def load_persisted_pine_signals(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Read worker-persisted Pine actions from KvStore when available."""
    out: dict[str, dict[str, Any]] = {}
    try:
        from options_portal_persistence.kv_store import KvStore

        kv = KvStore()
        if not kv.available:
            return out
        for sym in symbols:
            key = str(sym).upper()
            payload = kv.get(f"pine.signal.{key}")
            if isinstance(payload, dict) and payload.get("action"):
                out[key] = pine_row_from_kv(payload)
    except Exception:  # noqa: BLE001
        return out
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
            row.setdefault("signal_engine", "pine_nifty50_v2")
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
    for key, row in seeds.items():
        if key not in seen:
            r = dict(row)
            r["action"] = _normalize_pine_action(r.get("action"))
            rows.append(r)
    return rows
