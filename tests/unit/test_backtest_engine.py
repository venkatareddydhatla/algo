"""Dry-run backtest engine regression — real history preferred; synthetic refused."""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
API_APP = ROOT / "apps" / "api"
if str(API_APP) not in sys.path:
    sys.path.insert(0, str(API_APP))

from options_portal_backtest.candles import (  # noqa: E402
    RealHistoryUnavailable,
    resolve_candles,
    synthesize_candles,
)
from options_portal_backtest.engine import run_backtest  # noqa: E402
from options_portal_domain.types import Candle  # noqa: E402


def _fake_upstox_bars(symbol: str, n: int = 80) -> list[Candle]:
    """Chronological 15m bars labeled as if from Upstox."""
    start = datetime(2024, 1, 2, 3, 45, tzinfo=timezone.utc)  # 09:15 IST
    out: list[Candle] = []
    px = 500.0
    for i in range(n):
        ts = start + timedelta(minutes=15 * i)
        # skip weekend-ish by staying in weekday block via continuous minutes
        o = px
        c = px * (1.0 + ((i % 5) - 2) * 0.001)
        out.append(
            Candle(
                symbol=symbol.upper(),
                timeframe="15m",
                ts=ts,
                open=round(o, 4),
                high=round(max(o, c) * 1.001, 4),
                low=round(min(o, c) * 0.999, 4),
                close=round(c, 4),
                volume=1000 + i,
            )
        )
        px = c
    return out


def test_synthesize_deterministic():
    a = synthesize_candles("NIFTY", start=date(2024, 1, 1), end=date(2024, 1, 10), bars=40)
    b = synthesize_candles("NIFTY", start=date(2024, 1, 1), end=date(2024, 1, 10), bars=40)
    assert len(a) == 40
    assert [c.close for c in a] == [c.close for c in b]


def test_resolve_refuses_synthetic_by_default():
    with pytest.raises(RealHistoryUnavailable, match="No real history for RVNL"):
        resolve_candles("RVNL", bars=80, allow_synthetic=False)


def test_resolve_allow_synthetic_labels_source():
    candles, source = resolve_candles("RVNL", bars=80, allow_synthetic=True)
    assert source == "synthetic"
    assert len(candles) >= 40


def test_run_backtest_refuses_synthetic_by_default():
    with pytest.raises(RealHistoryUnavailable, match="No real history for RVNL"):
        run_backtest("RVNL", bars=80, dry_run=True)


def test_run_backtest_dry_run_summary_with_real_bars():
    bars = _fake_upstox_bars("RELIANCE", 120)
    result = run_backtest(
        "RELIANCE",
        bars=120,
        dry_run=True,
        candles=bars,
        candle_source="upstox",
    )
    assert result.dry_run is True
    assert result.mode == "dry_run"
    assert result.symbol == "RELIANCE"
    assert result.source == "upstox"
    assert result.metrics_are_synthetic is False
    assert result.win_rate_label is None
    assert result.pnl_label is None
    assert result.bars >= 50
    assert result.bars_evaluated > 0
    assert result.trades >= 0
    assert 0.0 <= result.win_rate <= 1.0
    assert isinstance(result.pnl, float)
    assert result.max_drawdown >= 0
    payload = result.to_dict()
    assert "fills" in payload
    assert payload["dry_run"] is True
    assert payload["source"] == "upstox"


def test_run_backtest_synthetic_opt_in_zeros_headline_metrics():
    result = run_backtest("NIFTY", bars=120, dry_run=True, allow_synthetic=True)
    assert result.source == "synthetic"
    assert result.metrics_are_synthetic is True
    assert result.win_rate_label == "synthetic — not tradeable"
    assert result.pnl_label == "synthetic — not tradeable"
    assert result.win_rate == 0.0
    assert result.pnl == 0.0
    assert "SYNTHETIC" in result.note.upper()
    assert "NOT TRADEABLE" in result.note.upper()


def test_fills_include_ist_entry_exit_dates():
    """Synthetic bars must yield non-blank IST entry/exit timestamps on fills."""
    from options_portal_backtest.engine import format_bar_ts_ist

    candles = synthesize_candles("NIFTY", start=date(2024, 1, 2), end=date(2024, 1, 20), bars=80)
    assert candles
    iso, display = format_bar_ts_ist(candles[0].ts)
    assert "+05:30" in iso or iso.endswith("+0530")
    assert display.endswith("IST")
    assert display[:4].isdigit()

    result = run_backtest("NIFTY", bars=200, dry_run=True, allow_synthetic=True)
    for fill in result.fills:
        assert fill["entry_ts"]
        assert fill["exit_ts"]
        assert fill["entry_at_ist"].endswith("IST")
        assert fill["exit_at_ist"].endswith("IST")
        assert fill["entry_ts"] <= fill["exit_ts"]
        assert ":" in fill["entry_at_ist"]
        assert ":" in fill["exit_at_ist"]
    if result.trades == 0:
        assert result.bars >= 50


def test_run_backtest_forces_dry_run():
    bars = _fake_upstox_bars("RELIANCE", 80)
    result = run_backtest(
        "RELIANCE",
        bars=80,
        dry_run=False,
        candles=bars,
        candle_source="upstox",
    )
    assert result.dry_run is True
    assert result.mode == "dry_run"


def test_incomplete_preloaded_refused():
    short = _fake_upstox_bars("RVNL", 10)
    with pytest.raises(RealHistoryUnavailable, match="incomplete history"):
        run_backtest(
            "RVNL",
            bars=80,
            candles=short,
            candle_source="upstox",
            allow_synthetic=False,
            min_bars=40,
        )


def test_backtest_api_route_rejects_live(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    async def _no_history(symbol, start, end):
        return [], "unavailable", "no UPSTOX_ACCESS_TOKEN"

    monkeypatch.setattr(
        "app.routes.backtest._fetch_upstox_candles", _no_history
    )

    client = TestClient(app)
    res = client.post(
        "/api/v1/backtest/run",
        json={"symbol": "NIFTY", "bars": 80, "dry_run": False},
    )
    assert res.status_code == 400
    assert "dry_run" in res.json()["detail"].lower()


def test_backtest_api_refuses_synthetic_by_default(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    async def _no_history(symbol, start, end):
        return [], "unavailable", "no UPSTOX_ACCESS_TOKEN"

    monkeypatch.setattr(
        "app.routes.backtest._fetch_upstox_candles", _no_history
    )

    client = TestClient(app)
    res = client.post(
        "/api/v1/backtest/run",
        json={"symbol": "RVNL", "bars": 80, "dry_run": True},
    )
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert "No real history for RVNL" in detail
    assert "Upstox" in detail


def test_backtest_api_get_ok_with_upstox(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    fake = _fake_upstox_bars("NIFTY", 200)

    async def _history(symbol, start, end):
        return fake, "upstox", "live candles"

    monkeypatch.setattr("app.routes.backtest._fetch_upstox_candles", _history)

    client = TestClient(app)
    res = client.get("/api/v1/backtest/run", params={"symbol": "NIFTY", "bars": 200})
    assert res.status_code == 200
    body = res.json()
    assert body["symbol"] == "NIFTY"
    assert body["dry_run"] is True
    assert body["source"] == "upstox"
    assert body["metrics_are_synthetic"] is False
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


def test_backtest_api_allow_synthetic_labels(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    async def _no_history(symbol, start, end):
        return [], "unavailable", "no UPSTOX_ACCESS_TOKEN"

    monkeypatch.setattr(
        "app.routes.backtest._fetch_upstox_candles", _no_history
    )

    client = TestClient(app)
    res = client.post(
        "/api/v1/backtest/run",
        json={
            "symbol": "RVNL",
            "bars": 80,
            "dry_run": True,
            "allow_synthetic": True,
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["source"] == "synthetic"
    assert body["metrics_are_synthetic"] is True
    assert body["win_rate_label"] == "synthetic — not tradeable"
    assert body["pnl_label"] == "synthetic — not tradeable"
    assert body["win_rate"] == 0.0
    assert body["pnl"] == 0.0


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
