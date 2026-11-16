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
