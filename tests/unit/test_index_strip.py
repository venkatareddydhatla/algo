"""NIFTY/SENSEX watchlist strip + cash market OPEN/CLOSE status."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from options_portal_domain.session_gates import SoftEodPolicy, is_cash_market_open
from options_portal_broker.adapter import select_quote_snapshot_from_data
from options_portal_broker.instruments import resolve_instrument_key


def test_sensex_instrument_key():
    assert resolve_instrument_key("SENSEX") == "BSE_INDEX|SENSEX"
    assert resolve_instrument_key("NIFTY") == "NSE_INDEX|Nifty 50"


def test_is_cash_market_open_uses_close_not_entry_cutoff():
    policy = SoftEodPolicy(open_ist="09:15", close_ist="15:30", no_new_entries_after="15:20")
    mid = datetime(2026, 11, 16, 12, 0, 0)
    assert is_cash_market_open(mid, policy=policy, trading_day=True) is True

    # Entry gate closed at 15:20, but cash market still OPEN until 15:30
    after_entries = datetime(2026, 11, 16, 15, 25, 0)
    assert is_cash_market_open(after_entries, policy=policy, trading_day=True) is True

    at_close = datetime(2026, 11, 16, 15, 30, 0)
    assert is_cash_market_open(at_close, policy=policy, trading_day=True) is False

    weekend = datetime(2026, 11, 15, 12, 0, 0)  # Sunday
    assert is_cash_market_open(weekend, policy=policy, trading_day=False) is False

    preopen = datetime(2026, 11, 16, 9, 0, 0)
    assert is_cash_market_open(preopen, policy=policy, trading_day=True) is False


def test_select_quote_snapshot_change_from_net_change():
    data = {
        "NSE_INDEX:Nifty 50": {
            "instrument_token": "NSE_INDEX|Nifty 50",
            "last_price": 24500.0,
            "net_change": 125.5,
        }
    }
    snap = select_quote_snapshot_from_data(
        data, symbol="NIFTY", instrument_key="NSE_INDEX|Nifty 50"
    )
    assert snap is not None
    assert snap["last_price"] == 24500.0
    assert snap["change"] == pytest.approx(125.5)
    assert snap["change_pct"] == pytest.approx(125.5 / (24500.0 - 125.5) * 100.0)


def test_select_quote_snapshot_change_from_ohlc_close():
    data = {
        "BSE_INDEX:SENSEX": {
            "instrument_key": "BSE_INDEX|SENSEX",
            "last_price": 82000.0,
            "ohlc": {"close": 81000.0},
        }
    }
    snap = select_quote_snapshot_from_data(
        data, symbol="SENSEX", instrument_key="BSE_INDEX|SENSEX"
    )
    assert snap is not None
    assert snap["last_price"] == 82000.0
    assert snap["change"] == pytest.approx(1000.0)
    assert snap["change_pct"] == pytest.approx(1000.0 / 81000.0 * 100.0)


def test_market_indices_endpoint_never_invents_prices(monkeypatch):
    from app import index_quotes

    index_quotes.clear_index_quote_cache()

    async def _empty():
        return {}

    monkeypatch.setattr(index_quotes, "fetch_live_index_quotes", AsyncMock(return_value={}))
    monkeypatch.setattr(
        index_quotes,
        "is_cash_market_open",
        lambda *a, **k: False,
    )
    monkeypatch.setattr(
        index_quotes,
        "is_nse_trading_day",
        lambda *a, **k: True,
    )

    from app.main import app

    client = TestClient(app)
    res = client.get("/api/v1/market/indices")
    assert res.status_code == 200
    body = res.json()
    assert body["market_status"] == "CLOSE"
    assert body["poll_hint_sec"] == 120
    assert len(body["indices"]) == 2
    for row in body["indices"]:
        assert row["symbol"] in {"NIFTY", "SENSEX"}
        assert row["last_price"] is None


def test_market_indices_endpoint_open_with_live(monkeypatch):
    from app import index_quotes

    index_quotes.clear_index_quote_cache()
    live = {
        "NIFTY": {
            "symbol": "NIFTY",
            "last_price": 24512.3,
            "change": 10.0,
            "change_pct": 0.04,
            "source": "upstox",
            "stale": False,
        },
        "SENSEX": {
            "symbol": "SENSEX",
            "last_price": 80123.4,
            "change": -20.0,
            "change_pct": -0.02,
            "source": "upstox",
            "stale": False,
        },
    }
    monkeypatch.setattr(
        index_quotes, "fetch_live_index_quotes", AsyncMock(return_value=live)
    )
    monkeypatch.setattr(index_quotes, "is_cash_market_open", lambda *a, **k: True)
    monkeypatch.setattr(index_quotes, "is_nse_trading_day", lambda *a, **k: True)

    from app.main import app

    client = TestClient(app)
    res = client.get("/api/v1/market/indices")
    assert res.status_code == 200
    body = res.json()
    assert body["market_status"] == "OPEN"
    assert body["poll_hint_sec"] == 15
    by_sym = {r["symbol"]: r for r in body["indices"]}
    assert by_sym["NIFTY"]["last_price"] == 24512.3
    assert by_sym["SENSEX"]["last_price"] == 80123.4
    assert by_sym["NIFTY"]["stale"] is False


def test_close_marks_live_as_stale_and_keeps_cache(monkeypatch):
    from app import index_quotes

    index_quotes.clear_index_quote_cache()
    index_quotes.remember_index_quotes(
        {
            "NIFTY": {"last_price": 24000.0, "change": 5.0, "source": "upstox"},
        }
    )
    monkeypatch.setattr(
        index_quotes, "fetch_live_index_quotes", AsyncMock(return_value={})
    )
    monkeypatch.setattr(index_quotes, "is_cash_market_open", lambda *a, **k: False)
    monkeypatch.setattr(index_quotes, "is_nse_trading_day", lambda *a, **k: True)

    from app.main import app

    client = TestClient(app)
    body = client.get("/api/v1/market/indices").json()
    assert body["market_status"] == "CLOSE"
    by_sym = {r["symbol"]: r for r in body["indices"]}
    assert by_sym["NIFTY"]["last_price"] == 24000.0
    assert by_sym["NIFTY"]["stale"] is True
    assert by_sym["NIFTY"]["source"] == "last_known"
    assert by_sym["SENSEX"]["last_price"] is None
