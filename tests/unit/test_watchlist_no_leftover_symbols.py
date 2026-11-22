"""Curated watchlist signals must not leak old seed / index leftovers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]


def test_build_universe_signals_stays_on_requested_symbols(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(ROOT)
    monkeypatch.setenv("CONFIG_PATH", str(ROOT / "config" / "local.yaml"))

    from app.mock_data import MOCK_SIGNALS, build_universe_signals
    from options_portal_domain.config import load_config, watchlist_symbols
    from options_portal_domain.universe import INDEX_SYMBOLS

    cfg = load_config()
    watch = watchlist_symbols(cfg)
    assert len(watch) == 18

    rows = build_universe_signals(watch)
    symbols = {str(r["symbol"]).upper() for r in rows}
    assert symbols == set(watch)
    for leftover in ("NIFTY", "BANKNIFTY", "TCS", "RELIANCE"):
        assert leftover not in symbols
    assert not (symbols & set(INDEX_SYMBOLS))

    seed_syms = {str(r["symbol"]).upper() for r in MOCK_SIGNALS}
    assert "NIFTY" not in seed_syms
    assert "BANKNIFTY" not in seed_syms
    assert "TCS" not in seed_syms
    assert seed_syms <= set(watch)


def test_signals_api_omits_reason_and_indices():
    from app.main import app

    sample = [
        {
            "id": "pine-nifty",
            "symbol": "NIFTY",
            "action": "HOLD",
            "trend": "NEUTRAL",
            "hybrid_score": 0.0,
            "confidence": 0.2,
            "reason_kind": "pine",
            "reason_summary": "index blocked",
            "reason_detail": "Technical:\n- index",
            "reasons": ["index"],
        },
        {
            "id": "pine-baj",
            "symbol": "BAJFINANCE",
            "action": "BUY",
            "trend": "BULLISH",
            "hybrid_score": 1.0,
            "confidence": 0.9,
            "reason_kind": "pine",
            "reason_summary": "U1",
            "reason_detail": "Technical:\n- U1",
            "reasons": ["U1"],
            "updated_at": "2026-11-22T10:00:00+00:00",
        },
    ]

    async def _fake_enrich(rows):
        for row in rows:
            row["price"] = 100.0
            row["ltp"] = 100.0
            row["price_source"] = "ltp"
        return {
            "marks_applied": [r["symbol"] for r in rows],
            "price_sources": {"ltp": len(rows), "candle_close": 0, "last_known": 0},
        }

    with patch(
        "app.routes.signals._load_signal_rows",
        return_value=([dict(r) for r in sample], "test"),
    ), patch(
        "app.routes.signals.enrich_rows_with_prices",
        new=AsyncMock(side_effect=_fake_enrich),
    ), patch(
        "app.routes.signals.min_equity_price_from_config",
        return_value=40.0,
    ):
        client = TestClient(app)
        res = client.get("/api/v1/signals", params={"limit": 50})
        assert res.status_code == 200
        body = res.json()
        symbols = [r["symbol"] for r in body["items"]]
        assert "NIFTY" not in symbols
        assert "BAJFINANCE" in symbols
        row = next(r for r in body["items"] if r["symbol"] == "BAJFINANCE")
        assert "reason_summary" not in row
        assert "reason_kind" not in row
        assert "reasons" not in row
        assert "reason_detail" not in row
        assert row["action"] == "BUY"
        assert row["trend"] == "BULLISH"
