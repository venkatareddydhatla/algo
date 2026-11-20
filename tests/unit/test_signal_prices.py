"""Signals page price enrichment: LTP → candle close → last-known."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
API_APP = ROOT / "apps" / "api"
if str(API_APP) not in sys.path:
    sys.path.insert(0, str(API_APP))

from app.marks import enrich_rows_with_prices, price_marks_for_symbols  # noqa: E402


@pytest.mark.asyncio
async def test_price_marks_prefer_ltp_per_symbol():
    with patch(
        "app.marks.live_ltp_map",
        new=AsyncMock(return_value={"IOC": 144.04, "360ONE": 996.96}),
    ), patch(
        "app.marks.candle_close_map",
        new=AsyncMock(return_value={}),
    ):
        prices, sources = await price_marks_for_symbols(["IOC", "360ONE", "BPCL"])

    assert prices["IOC"] == 144.04
    assert prices["360ONE"] == 996.96
    assert sources["IOC"] == "ltp"
    assert sources["360ONE"] == "ltp"
    assert sources["BPCL"] == "last_known"
    assert prices["BPCL"] > 0
    # Never reuse 360ONE mark for BPCL / IOC
    assert prices["IOC"] != prices["360ONE"]
    assert prices["BPCL"] != prices["360ONE"]


@pytest.mark.asyncio
async def test_price_marks_candle_close_when_ltp_missing():
    with patch(
        "app.marks.live_ltp_map",
        new=AsyncMock(return_value={}),
    ), patch(
        "app.marks.candle_close_map",
        new=AsyncMock(return_value={"RELIANCE": 2910.5}),
    ):
        prices, sources = await price_marks_for_symbols(["RELIANCE"])

    assert prices["RELIANCE"] == 2910.5
    assert sources["RELIANCE"] == "candle_close"


@pytest.mark.asyncio
async def test_enrich_rows_sets_price_and_ltp_fields():
    rows = [{"symbol": "NIFTY"}, {"symbol": "TCS"}]
    with patch(
        "app.marks.live_ltp_map",
        new=AsyncMock(return_value={"NIFTY": 24512.3}),
    ), patch(
        "app.marks.candle_close_map",
        new=AsyncMock(return_value={}),
    ):
        meta = await enrich_rows_with_prices(rows)

    assert rows[0]["price"] == 24512.3
    assert rows[0]["ltp"] == 24512.3
    assert rows[0]["price_source"] == "ltp"
    assert rows[1]["price"] is not None
    assert rows[1]["ltp"] is None
    assert rows[1]["price_source"] == "last_known"
    assert "NIFTY" in meta["marks_applied"]
    assert meta["price_sources"]["ltp"] == 1
    assert meta["price_sources"]["last_known"] == 1


@pytest.mark.asyncio
async def test_signals_api_drops_sub_min_equity_price_from_table_and_top():
    """IDEA @ ₹12.05 must not appear in /signals or TOP BUY after price floor."""
    from fastapi.testclient import TestClient

    from app.main import app

    cheap = {
        "id": "pine-idea",
        "symbol": "IDEA",
        "action": "BUY",
        "hybrid_score": 1.0,
        "confidence": 0.9,
        "reason_kind": "pine",
    }
    rich = {
        "id": "pine-reliance",
        "symbol": "RELIANCE",
        "action": "BUY",
        "hybrid_score": 0.8,
        "confidence": 0.8,
        "reason_kind": "pine",
    }

    async def _fake_enrich(rows):
        for row in rows:
            sym = str(row.get("symbol") or "").upper()
            if sym == "IDEA":
                row["price"] = 12.05
                row["ltp"] = 12.05
                row["price_source"] = "ltp"
            elif sym == "RELIANCE":
                row["price"] = 1400.0
                row["ltp"] = 1400.0
                row["price_source"] = "ltp"
            else:
                row["price"] = 100.0
                row["ltp"] = 100.0
                row["price_source"] = "ltp"
        return {
            "marks_applied": ["IDEA", "RELIANCE"],
            "price_sources": {"ltp": len(rows), "candle_close": 0, "last_known": 0},
        }

    with patch(
        "app.routes.signals._load_signal_rows",
        return_value=([dict(cheap), dict(rich)], "test"),
    ), patch(
        "app.routes.signals.enrich_rows_with_prices",
        new=AsyncMock(side_effect=_fake_enrich),
    ), patch(
        "app.routes.signals.clear_persisted_pine_signals",
        return_value=["IDEA"],
    ), patch(
        "app.routes.signals.min_equity_price_from_config",
        return_value=20.0,
    ):
        client = TestClient(app)
        table = client.get("/api/v1/signals", params={"limit": 50})
        assert table.status_code == 200
        body = table.json()
        symbols = [r["symbol"] for r in body["items"]]
        assert "IDEA" not in symbols
        assert "RELIANCE" in symbols
        assert "IDEA" in body.get("price_floor_dropped", [])

        top = client.get("/api/v1/signals/top-hybrid", params={"limit": 5})
        assert top.status_code == 200
        top_body = top.json()
        ce_syms = [r["symbol"] for r in top_body["ce"]]
        assert "IDEA" not in ce_syms
        assert "RELIANCE" in ce_syms
        assert "IDEA" in top_body.get("price_floor_dropped", [])


def test_clear_persisted_pine_signals_deletes_kv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from options_portal_persistence.kv_store import KvStore

    from app.mock_data import clear_persisted_pine_signals, load_persisted_pine_signals

    db = tmp_path / "kv.sqlite"
    monkeypatch.setenv("PERSISTENCE_PROFILE", "local")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    kv = KvStore(url=f"sqlite:///{db}")
    assert kv.available
    kv.set(
        "pine.signal.IDEA",
        {
            "symbol": "IDEA",
            "action": "BUY",
            "score": 1.0,
            "confidence": 0.9,
            "reasons": ["stale"],
        },
    )
    assert "IDEA" in load_persisted_pine_signals(["IDEA"])
    cleared = clear_persisted_pine_signals(["IDEA"])
    assert cleared == ["IDEA"]
    assert load_persisted_pine_signals(["IDEA"]) == {}
