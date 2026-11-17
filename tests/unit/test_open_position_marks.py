"""Open-position uPnL marks: live LTP + last-known retention on tick gaps."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
API_APP = ROOT / "apps" / "api"
if str(API_APP) not in sys.path:
    sys.path.insert(0, str(API_APP))

from app.marks import (  # noqa: E402
    clear_last_known_ltp_cache,
    open_position_marks_for_symbols,
    remember_ltp_marks,
)


@pytest.fixture
def paper_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "paper.sqlite"
    monkeypatch.setenv("PERSISTENCE_PROFILE", "local")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    yield db


@pytest.fixture(autouse=True)
def _clean_ltp_cache():
    clear_last_known_ltp_cache()
    yield
    clear_last_known_ltp_cache()


@pytest.mark.asyncio
async def test_open_marks_prefer_live_ltp_per_symbol():
    with patch(
        "app.marks.live_ltp_map",
        new=AsyncMock(return_value={"IOC": 144.04, "360ONE": 996.96, "BPCL": 348.75}),
    ):
        prices, sources = await open_position_marks_for_symbols(
            ["IOC", "360ONE", "BPCL"]
        )

    assert prices == {"BPCL": 348.75, "IOC": 144.04, "360ONE": 996.96}
    assert sources == {"BPCL": "ltp", "IOC": "ltp", "360ONE": "ltp"}
    # Never reuse one symbol's mark for another
    assert len(set(prices.values())) == 3


@pytest.mark.asyncio
async def test_partial_quote_failure_keeps_successful_and_last_known():
    """One failed tick must not zero the whole open-positions table."""
    remember_ltp_marks({"IOC": 145.0, "BPCL": 340.0, "360ONE": 1000.0})

    with patch(
        "app.marks.live_ltp_map",
        # Partial success: IOC live, BPCL/360ONE missing this tick
        new=AsyncMock(return_value={"IOC": 144.04}),
    ):
        prices, sources = await open_position_marks_for_symbols(
            ["IOC", "BPCL", "360ONE"]
        )

    assert prices["IOC"] == pytest.approx(144.04)
    assert sources["IOC"] == "ltp"
    assert prices["BPCL"] == pytest.approx(340.0)
    assert sources["BPCL"] == "last_known"
    assert prices["360ONE"] == pytest.approx(1000.0)
    assert sources["360ONE"] == "last_known"


@pytest.mark.asyncio
async def test_total_live_failure_keeps_all_last_known():
    """Full Upstox batch miss still serves retained marks (no all-₹0 table)."""
    remember_ltp_marks({"RELIANCE": 2910.5, "TCS": 4120.0})

    with patch(
        "app.marks.live_ltp_map",
        new=AsyncMock(return_value={}),
    ):
        prices, sources = await open_position_marks_for_symbols(["RELIANCE", "TCS"])

    assert prices["RELIANCE"] == pytest.approx(2910.5)
    assert prices["TCS"] == pytest.approx(4120.0)
    assert sources["RELIANCE"] == "last_known"
    assert sources["TCS"] == "last_known"


@pytest.mark.asyncio
async def test_live_ltp_map_aclose_failure_still_returns_marks():
    """aclose() raising after a successful map must not discard LTPs."""
    from app.marks import live_ltp_map

    class _Broker:
        async def get_ltp_map(self, symbols):
            return {str(s).upper(): 111.0 + i for i, s in enumerate(symbols)}

        async def aclose(self):
            raise RuntimeError("aclose boom")

    with patch("options_portal_broker.adapter.UpstoxAdapter", return_value=_Broker()):
        marks = await live_ltp_map(["IOC", "BPCL"])

    # live_ltp_map sorts symbols; aclose failure must still return the map
    assert marks["BPCL"] == pytest.approx(111.0)
    assert marks["IOC"] == pytest.approx(112.0)
    assert len(marks) == 2


@pytest.mark.asyncio
async def test_trades_summary_uses_last_known_on_live_gap(paper_env: Path):
    from options_portal_domain.equity import plan_equity_intents
    from options_portal_domain.types import OrderResult, SignalAction
    from options_portal_persistence.paper_trades import PaperTradeStore

    store = PaperTradeStore()
    for sym, px in (("IOC", 144.0), ("BPCL", 340.0)):
        intent = plan_equity_intents(
            sym,
            SignalAction.BUY_CE_BIAS,
            open_side=None,
            open_qty=0,
            qty=2,
            price=px,
            reason="upnl cache test",
        )[0]
        store.record_simulated_fill(
            intent,
            OrderResult(f"id-{sym}", "simulated", "dry_run", f"SIM-{sym}", None),
        )

    remember_ltp_marks({"IOC": 150.0, "BPCL": 350.0})

    with patch(
        "app.marks.live_ltp_map",
        new=AsyncMock(return_value={"IOC": 151.0}),  # BPCL tick missing
    ):
        from app.routes.trades import trades_summary

        payload = await trades_summary()

    by_sym = {p["symbol"]: p for p in payload["positions"]}
    assert by_sym["IOC"]["mark_source"] == "ltp"
    assert by_sym["IOC"]["mark_price"] == pytest.approx(151.0)
    assert by_sym["IOC"]["unrealized_pnl"] == pytest.approx((151.0 - 144.0) * 2)
    assert by_sym["BPCL"]["mark_source"] == "last_known"
    assert by_sym["BPCL"]["mark_price"] == pytest.approx(350.0)
    assert by_sym["BPCL"]["unrealized_pnl"] == pytest.approx((350.0 - 340.0) * 2)
    assert "IOC" in payload["marks_applied"]
    assert "BPCL" in payload["marks_last_known"]
    assert payload["mark_gaps"] == []
