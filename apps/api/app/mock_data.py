"""In-memory mock payloads so the UI runs without a full DB."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

NOW = datetime.now(tz=timezone.utc).isoformat()

MOCK_SIGNALS: list[dict[str, Any]] = [
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
        "reasons": ["Hybrid in neutral band"],
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
        "reasons": ["EMA9 < EMA21", "Close below VWAP", "Bearish news cluster"],
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
        "reasons": ["News/TA conflict — hold"],
    },
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
        "note": "Live mode switch is UI-present but placement remains gated in v1",
    },
    "hybrid": {
        "weights": {"tech": 0.65, "news": 0.35},
        "enter_long": 0.45,
        "enter_short": -0.45,
        "news_veto_abs": 0.70,
    },
}
