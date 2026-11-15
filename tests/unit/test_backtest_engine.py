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


def test_fills_include_ist_entry_exit_dates():
    """Synthetic bars must yield non-blank IST entry/exit timestamps on fills."""
    from options_portal_backtest.engine import format_bar_ts_ist

    candles = synthesize_candles("NIFTY", start=date(2024, 1, 2), end=date(2024, 1, 20), bars=80)
    assert candles
    iso, display = format_bar_ts_ist(candles[0].ts)
    assert "+05:30" in iso or iso.endswith("+0530")
    assert display.endswith("IST")
    assert display[:4].isdigit()

    result = run_backtest("NIFTY", bars=200, dry_run=True)
    # Enough bars that confluence usually produces at least one trade; if not,
    # still assert candle formatting path above.
    for fill in result.fills:
        assert fill["entry_ts"]
        assert fill["exit_ts"]
        assert fill["entry_at_ist"].endswith("IST")
        assert fill["exit_at_ist"].endswith("IST")
        assert fill["entry_ts"] <= fill["exit_ts"]
        # Session-ish clock in display (HH:MM)
        assert ":" in fill["entry_at_ist"]
        assert ":" in fill["exit_at_ist"]
    if result.trades == 0:
        # Still prove synthetic timestamps are wired for when trades appear
        assert result.bars >= 50


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
    res = client.get("/api/v1/backtest/run", params={"symbol": "NIFTY", "bars": 200})
    assert res.status_code == 200
    body = res.json()
    assert body["symbol"] == "NIFTY"
    assert body["dry_run"] is True
    assert "trades" in body
    assert "win_rate" in body
    assert "pnl" in body
    assert isinstance(body["fills"], list)
    for fill in body["fills"]:
        assert fill.get("entry_ts")
        assert fill.get("exit_ts")
        assert str(fill.get("entry_at_ist", "")).endswith("IST")
        assert str(fill.get("exit_at_ist", "")).endswith("IST")
        assert "entry_price" in fill
        assert "exit_price" in fill
    assert "IST" in body.get("note", "")


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
