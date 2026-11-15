"""In-memory mock payloads so the UI runs without a full DB.

Dry-run path synthesizes non-placeholder hybrid scores for every watchlist
symbol so the portal can surface actionable F&O names across the full universe.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from options_portal_domain.universe import INDEX_SYMBOLS
from options_portal_hybrid.explain import attach_reason_fields, build_reason_fields

NOW = datetime.now(tz=timezone.utc).isoformat()

# Hand-tuned showcase rows (also used as overrides when expanding the universe).
_SEED_SIGNALS: list[dict[str, Any]] = [
    {
        "id": "sig-nifty-1",
        "symbol": "NIFTY",
        "timeframe": "15m",
        "action": "BUY_CE_BIAS",
        "hybrid_score": 0.62,
        "tech_score": 0.71,
        "news_score": 0.38,
        "confidence": 0.74,
        "expiry_hint": "weekly",
        "updated_at": NOW,
        "reasons": [
            "EMA9 > EMA21 on 15m",
            "RSI(14) 58 — rising",
            "Supertrend bullish",
            "News: mild positive last 3h",
            "Headline: Foreign inflows lift benchmark indices into close",
        ],
    },
    {
        "id": "sig-bn-1",
        "symbol": "BANKNIFTY",
        "timeframe": "15m",
        "action": "HOLD",
        "hybrid_score": 0.12,
        "tech_score": 0.20,
        "news_score": -0.05,
        "confidence": 0.41,
        "expiry_hint": "weekly",
        "updated_at": NOW,
        "reasons": ["Hybrid in neutral band", "RSI(14) 51 — neutral"],
    },
    {
        "id": "sig-rel-1",
        "symbol": "RELIANCE",
        "timeframe": "15m",
        "action": "BUY_PE_BIAS",
        "hybrid_score": -0.55,
        "tech_score": -0.48,
        "news_score": -0.67,
        "confidence": 0.68,
        "expiry_hint": "monthly",
        "updated_at": NOW,
        "reasons": [
            "EMA9 < EMA21",
            "Close below VWAP",
            "Bearish news cluster",
            "Headline: Broker cuts target on Reliance amid margin pressure",
        ],
    },
    {
        "id": "sig-tcs-1",
        "symbol": "TCS",
        "timeframe": "15m",
        "action": "HOLD",
        "hybrid_score": -0.08,
        "tech_score": 0.10,
        "news_score": -0.35,
        "confidence": 0.36,
        "expiry_hint": "monthly",
        "updated_at": NOW,
        "reasons": ["News/TA conflict — hold", "RSI(14) 48 — neutral"],
    },
]

MOCK_SIGNALS: list[dict[str, Any]] = [
    attach_reason_fields(row) for row in _SEED_SIGNALS
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
            "qty": 25,
            "avg_price": 138.0,
            "unrealized_pnl": 1125.0,
        },
        {
            "symbol": "RELIANCE 2920 PE",
            "qty": 250,
            "avg_price": 64.5,
            "unrealized_pnl": -2400.0,
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


def synthetic_signal_for_symbol(
    symbol: str,
    *,
    timeframe: str = "15m",
    updated_at: str | None = None,
    enter_long: float = 0.45,
    enter_short: float = -0.45,
    live_news: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a non-placeholder dry-run hybrid score for one underlying.

    When ``live_news`` is present for the symbol, use real VADER scores and
    headlines instead of synthetic dry-run news filler.
    """
    sym = symbol.upper()
    # Map hash into [-0.85, 0.85] tech / news so a share of names clear entry bands.
    tech_raw = (_stable_unit(sym, "tech") - 0.5) * 1.7
    tech_score = max(-1.0, min(1.0, round(tech_raw, 4)))

    live = list(live_news or [])
    if live:
        news_score, news_conf, headlines = _aggregate_live_news(live)
        used_live_news = True
    else:
        news_raw = (_stable_unit(sym, "news") - 0.5) * 1.5
        news_score = max(-1.0, min(1.0, round(news_raw, 4)))
        news_conf = 1.0 if abs(news_score) > 0 else 0.0
        headlines = []
        used_live_news = False

    hybrid = max(
        -1.0,
        min(1.0, round(0.65 * tech_score + 0.35 * news_score, 4)),
    )
    confidence = round(
        min(1.0, 0.35 + abs(hybrid) * 0.45 + _stable_unit(sym, "conf") * 0.2),
        4,
    )

    reasons: list[str] = []
    if tech_score >= 0.15:
        reasons.append("EMA9 > EMA21 on 15m")
        reasons.append(f"RSI(14) {50 + int(tech_score * 20)} - rising")
        if tech_score >= 0.35:
            reasons.append("Supertrend bullish")
    elif tech_score <= -0.15:
        reasons.append("EMA9 < EMA21")
        reasons.append(f"RSI(14) {50 + int(tech_score * 20)} - falling")
        if tech_score <= -0.35:
            reasons.append("Close below VWAP")
    else:
        reasons.append(f"RSI(14) {50 + int(tech_score * 10)} - neutral")

    weight_tech = 0.65
    weight_news = 0.35
    tech_alone = weight_tech * tech_score
    news_alone = weight_news * news_score
    # Only attach news bullets when news alone clears or tips the decision;
    # mild filler must not force a HYBRID label.
    news_tips = False
    if hybrid >= enter_long:
        action = "BUY_CE_BIAS"
        news_tips = tech_alone < enter_long
    elif hybrid <= enter_short:
        action = "BUY_PE_BIAS"
        news_tips = tech_alone > enter_short
    else:
        action = "HOLD"

    news_alone_clears = (
        (action == "BUY_CE_BIAS" and news_alone >= enter_long)
        or (action == "BUY_PE_BIAS" and news_alone <= enter_short)
    )
    include_news = action != "HOLD" and (news_tips or news_alone_clears)

    if used_live_news and (include_news or abs(news_score) >= 0.15):
        tone = "bullish" if news_score > 0 else "bearish" if news_score < 0 else "neutral"
        reasons.append(
            f"News: {tone} live RSS cluster ({len(live)} item(s), score {news_score:+.2f})"
        )
        for title in headlines[:2]:
            reasons.append(f"Headline: {title}")
    elif include_news and abs(news_score) >= 0.15:
        tone = "bullish" if news_score > 0 else "bearish"
        if abs(news_score) >= 0.35:
            reasons.append(f"News: {tone} cluster last 3h")
            reasons.append(
                f"Headline: Dry-run {tone} flow for {sym} "
                f"(score {news_score:+.2f})"
            )
        else:
            reasons.append(
                f"News: mild {'positive' if news_score > 0 else 'negative'} last 3h"
            )

    if action == "BUY_CE_BIAS":
        reasons.append(f"Hybrid {hybrid:.2f} >= enter_long {enter_long}")
    elif action == "BUY_PE_BIAS":
        reasons.append(f"Hybrid {hybrid:.2f} <= enter_short {enter_short}")
    else:
        reasons.append(f"Hybrid {hybrid:.2f} in neutral band")

    explain = build_reason_fields(
        action=action,
        tech_score=tech_score,
        news_score=news_score,
        hybrid_score=hybrid,
        reasons=reasons,
        weight_tech=weight_tech,
        weight_news=weight_news,
        enter_long=enter_long,
        enter_short=enter_short,
        tech_confidence=1.0,
        news_confidence=news_conf if (used_live_news or abs(news_score) > 0) else 0.0,
    )
    expiry = "weekly" if sym in INDEX_SYMBOLS else "monthly"
    return {
        "id": f"dry-{sym.lower()}",
        "symbol": sym,
        "timeframe": timeframe,
        "action": action,
        "hybrid_score": hybrid,
        "tech_score": tech_score,
        "news_score": news_score,
        "confidence": confidence,
        "expiry_hint": expiry,
        "updated_at": updated_at or NOW,
        "reasons": reasons,
        "stub": False,
        "dry_run_synthetic": True,
        "live_news": used_live_news,
        **explain,
    }


def build_universe_signals(
    symbols: list[str],
    *,
    timeframe: str = "15m",
    seed_rows: list[dict[str, Any]] | None = None,
    live_news_by_symbol: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Scores for the full watchlist: seed overrides + synthetic dry-run rows."""
    seeds = {r["symbol"].upper(): attach_reason_fields(dict(r)) for r in (seed_rows or MOCK_SIGNALS)}
    live_map = live_news_by_symbol or {}
    now = datetime.now(tz=timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sym in symbols:
        key = sym.upper()
        if key in seen:
            continue
        seen.add(key)
        live = live_map.get(key) or []
        if key in seeds and not live:
            row = dict(seeds[key])
            row["updated_at"] = now
            rows.append(attach_reason_fields(row))
        else:
            # Prefer live-news-aware synthetic row when RSS data exists for the symbol
            rows.append(
                synthetic_signal_for_symbol(
                    key,
                    timeframe=timeframe,
                    updated_at=now,
                    live_news=live or None,
                )
            )
    # Include any seed symbols not in the watchlist (demo leftovers)
    for key, row in seeds.items():
        if key not in seen:
            rows.append(attach_reason_fields(dict(row)))
    return rows
