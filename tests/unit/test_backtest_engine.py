"""Dry-run backtest engine regression."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_APP = ROOT / "apps" / "api"
if str(API_APP) not in sys.path:
    sys.path.insert(0, str(API_APP))

from options_portal_backtest.candles import synthesize_candles  # noqa: E402
from options_portal_backtest.engine import run_backtest  # noqa: E402


def test_synthesize_deterministic():
    a = synthesize_candles("NIFTY", start=date(2024, 1, 1), end=date(2024, 1, 10), bars=40)
    b = synthesize_candles("NIFTY", start=date(2024, 1, 1), end=date(2024, 1, 10), bars=40)
    assert len(a) == 40
    assert [c.close for c in a] == [c.close for c in b]


def test_run_backtest_dry_run_summary():
    result = run_backtest("NIFTY", bars=120, dry_run=True)
    assert result.dry_run is True
    assert result.mode == "dry_run"
    assert result.symbol == "NIFTY"
    assert result.bars >= 50
    assert result.bars_evaluated > 0
    assert result.trades >= 0
    assert 0.0 <= result.win_rate <= 1.0
    assert isinstance(result.pnl, float)
    assert result.max_drawdown >= 0
    payload = result.to_dict()
    assert "fills" in payload
    assert payload["dry_run"] is True


def test_run_backtest_forces_dry_run():
    result = run_backtest("RELIANCE", bars=80, dry_run=False)
    assert result.dry_run is True
    assert result.mode == "dry_run"


def test_backtest_api_route_rejects_live(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    res = client.post(
        "/api/v1/backtest/run",
        json={"symbol": "NIFTY", "bars": 80, "dry_run": False},
    )
    assert res.status_code == 400
    assert "dry_run" in res.json()["detail"].lower()


def test_backtest_api_get_ok():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    res = client.get("/api/v1/backtest/run", params={"symbol": "NIFTY", "bars": 80})
    assert res.status_code == 200
    body = res.json()
    assert body["symbol"] == "NIFTY"
    assert body["dry_run"] is True
    assert "trades" in body
    assert "win_rate" in body
    assert "pnl" in body


def test_top_hybrid_api():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    res = client.get("/api/v1/signals/top-hybrid", params={"limit": 3})
    assert res.status_code == 200
    body = res.json()
    assert "ce" in body and "pe" in body
    assert len(body["ce"]) <= 3
    assert len(body["pe"]) <= 3
    for row in body["ce"]:
        assert row["hybrid_score"] > 0
    for row in body["pe"]:
        assert row["hybrid_score"] < 0
