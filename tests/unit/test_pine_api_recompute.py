"""ALG-86/94: API pine refresh + pivot-aligned Action (high→SELL / low→BUY)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from options_portal_domain.pine_signals import (
    PineBarState,
    PineParams,
    enforce_bearish_pivot_sell_action,
    enforce_pivot_aligned_action,
    evaluate_pine_signal,
    latest_closed_bar_open,
    map_watchlist_action,
    map_trend,
)
from options_portal_domain.types import Candle, SignalAction


def _bar(
    i: int,
    *,
    close: float,
    symbol: str = "VEDL",
) -> Candle:
    ts0 = datetime(2026, 11, 14, 3, 45, tzinfo=timezone.utc)
    c = float(close)
    return Candle(
        symbol=symbol,
        timeframe="15m",
        ts=ts0 + timedelta(minutes=15 * i),
        open=c,
        high=c + 1.0,
        low=c - 1.0,
        close=c,
        volume=100_000,
    )


def _custom_params() -> PineParams:
    return PineParams(
        confirmation_mode="Confirmed Only",
        confirmation_bars=0,
        sensitivity="Custom",
        method="high_low",
        percent_amount=0.01,
        rev_amount=0.5,
        atr_reversal=0.1,
        atr_length=14,
        average_length=5,
        require_strong=False,
        action_no_hold=True,
        neutral_action="BUY",
    )


def test_hard_invariant_bearish_pivot_high_ignores_signal_sticky():
    """lastConfirmedPivotIsHigh → SELL even if signal=+1 (any Trend)."""
    p = _custom_params()
    state = PineBarState(
        ema9=1.0,
        ema14=1.2,
        ema21=1.4,
        atr=1.0,
        reversal_amount=1.0,
        direction=-1,
        signal=1,
        u1=False,
        d1=False,
        buysignal=0,
        sellsignal=1,
        strong_bullish=False,
        strong_bearish=False,
        confirmed_pivot=False,
        buy_signal=False,
        sell_signal=False,
        last_confirmed_pivot_is_high=True,
    )
    act, src, _ = map_watchlist_action(state, params=p)
    assert act == SignalAction.SELL
    assert src == "pivot"
    assert map_trend(state) == "BEARISH"
    assert (
        enforce_bearish_pivot_sell_action(
            "BUY",
            "BEARISH",
            features={"last_confirmed_pivot_is_high": True, "signal": 1},
        )
        == "SELL"
    )
    # BAJFINANCE class: BUY+BULLISH + pivot high → SELL
    assert (
        enforce_pivot_aligned_action(
            "BUY",
            "BULLISH",
            features={"last_confirmed_pivot_is_high": True, "signal": 1},
        )
        == "SELL"
    )
    # Symmetric: sticky SELL + pivot low → BUY
    assert (
        enforce_pivot_aligned_action(
            "SELL",
            "BEARISH",
            features={"last_confirmed_pivot_is_high": False, "signal": -1},
        )
        == "BUY"
    )


def test_api_invariant_gate_blocks_buy_bearish_pivot_high():
    from app.pine_refresh import enforce_bearish_pivot_invariant

    rows: list[dict[str, Any]] = [
        {
            "symbol": "VEDL",
            "action": "BUY",
            "pine_action": "BUY",
            "display_action": "BUY",
            "trend": "BEARISH",
            "features": {
                "last_confirmed_pivot_is_high": True,
                "signal": 1,
                "sellsignal": 1,
            },
        }
    ]
    forced = enforce_bearish_pivot_invariant(rows)
    assert forced == 1
    assert rows[0]["action"] == "SELL"
    assert rows[0]["pine_action"] == "SELL"
    assert rows[0]["display_action"] == "SELL"
    # Screenshot guarantee: no BUY+BEARISH+pivot-high remains.
    assert not (
        rows[0]["action"] == "BUY"
        and rows[0]["trend"] == "BEARISH"
        and rows[0]["features"]["last_confirmed_pivot_is_high"] is True
    )


def test_api_invariant_gate_blocks_bajfinance_buy_bullish_pivot_high():
    """ALG-94: BUY+BULLISH + lastConfirmedPivotIsHigh cannot leave the API."""
    from app.pine_refresh import enforce_bearish_pivot_invariant

    rows: list[dict[str, Any]] = [
        {
            "symbol": "BAJFINANCE",
            "action": "BUY",
            "pine_action": "BUY",
            "display_action": "BUY",
            "trend": "BULLISH",
            "features": {
                "last_confirmed_pivot_is_high": True,
                "signal": 1,
                "buysignal": 1,
            },
        }
    ]
    forced = enforce_bearish_pivot_invariant(rows)
    assert forced == 1
    assert rows[0]["action"] == "SELL"
    assert rows[0]["pine_action"] == "SELL"
    assert rows[0]["display_action"] == "SELL"
    assert rows[0]["trend"] == "BULLISH"  # Trend untouched
    assert not (
        rows[0]["action"] == "BUY"
        and rows[0]["features"]["last_confirmed_pivot_is_high"] is True
    )


def test_apply_pine_result_enforces_invariant_on_vedl_like_series():
    from app.pine_refresh import apply_pine_result_to_row, enforce_bearish_pivot_invariant

    closes: list[float] = []
    for i in range(30):
        closes.append(100.0 + i * 2.0)
    for i in range(20):
        closes.append(160.0 - i * 3.0)
    for i in range(25):
        closes.append(100.0 + i * 3.0)
    for i in range(25):
        closes.append(175.0 - i * 3.0)
    candles = [_bar(i, close=c) for i, c in enumerate(closes)]
    result = evaluate_pine_signal(
        "VEDL", candles, params=_custom_params(), prefer_closed=False
    )
    row: dict[str, Any] = {
        "symbol": "VEDL",
        "action": "BUY",  # stale KV
        "trend": "NEUTRAL",
        "features": {},
    }
    apply_pine_result_to_row(row, result, bar_ts=candles[-1].ts)
    enforce_bearish_pivot_invariant([row])
    pivot = (row.get("features") or {}).get("last_confirmed_pivot_is_high")
    # Action always follows last confirmed pivot after recompute + gate.
    if pivot is True:
        assert row["action"] == "SELL"
    elif pivot is False:
        assert row["action"] == "BUY"
    if row["trend"] == "BEARISH" and pivot is True:
        assert row["action"] == "SELL"
    # signal- without a later pivot-low flip still cannot leave BUY.
    if (row.get("features") or {}).get("signal", 0) < 0 and pivot is not False:
        assert row["action"] == "SELL"


def test_row_needs_recompute_for_buy_bearish():
    from app.pine_refresh import row_needs_candle_recompute

    assert row_needs_candle_recompute(
        {"action": "BUY", "trend": "BEARISH", "pine_persisted": True, "features": {}}
    )
    assert row_needs_candle_recompute(
        {
            "action": "SELL",
            "trend": "BEARISH",
            "pine_persisted": True,
            "features": {},  # missing pivot → refresh
        }
    )
    # Customs stuck on hybrid HOLD must always refresh → Reversal Pro BUY/SELL.
    assert row_needs_candle_recompute(
        {
            "action": "HOLD",
            "trend": "NEUTRAL",
            "pine_persisted": True,
            "signal_engine": "hybrid_confluence",
            "features": {},
        }
    )
    # BAJFINANCE: BUY+BULLISH + pivot high → must recompute
    assert row_needs_candle_recompute(
        {
            "action": "BUY",
            "trend": "BULLISH",
            "pine_persisted": True,
            "signal_engine": "reversal_pro_v32",
            "features": {"last_confirmed_pivot_is_high": True},
        }
    )
    # BUY without pivot evidence → recompute (cannot prove last pivot is low)
    assert row_needs_candle_recompute(
        {
            "action": "BUY",
            "trend": "BULLISH",
            "pine_persisted": True,
            "signal_engine": "reversal_pro_v32",
            "features": {"signal": 1},
            "bar_ts": latest_closed_bar_open().isoformat(),
        }
    )
    # Healthy SELL + pivot high + current bar → no recompute
    assert not row_needs_candle_recompute(
        {
            "action": "SELL",
            "trend": "BEARISH",
            "pine_persisted": True,
            "signal_engine": "reversal_pro_v32",
            "features": {"last_confirmed_pivot_is_high": True},
            "bar_ts": latest_closed_bar_open().isoformat(),
        }
    )


@pytest.mark.asyncio
async def test_refresh_rows_from_candles_flips_stale_buy(
    monkeypatch: pytest.MonkeyPatch,
):
    """API recompute path: synthetic candles flip stale BUY without worker."""
    from app import pine_refresh as pr

    closes: list[float] = []
    for i in range(30):
        closes.append(100.0 + i * 2.0)
    for i in range(20):
        closes.append(160.0 - i * 3.0)
    for i in range(25):
        closes.append(100.0 + i * 3.0)
    for i in range(30):
        closes.append(175.0 - i * 3.0)
    candles = [_bar(i, close=c) for i, c in enumerate(closes)]

    async def _fake_candles(symbols):
        return {"VEDL": candles}

    monkeypatch.setattr(pr, "persist_recomputed_pine_row", lambda row: None)
    monkeypatch.setattr(
        "app.marks._live_15m_candles_map",
        _fake_candles,
    )

    rows = [
        {
            "symbol": "VEDL",
            "action": "BUY",
            "pine_action": "BUY",
            "trend": "BEARISH",
            "pine_persisted": True,
            "features": {"signal": 1, "sellsignal": 1},  # stale; no pivot field
        }
    ]
    meta = await pr.refresh_rows_from_candles(rows, cfg={}, force_all=True)
    assert "VEDL" in meta["recomputed"]
    pr.enforce_bearish_pivot_invariant(rows)
    assert rows[0]["action"] != "BUY" or rows[0]["trend"] != "BEARISH" or (
        rows[0].get("features") or {}
    ).get("last_confirmed_pivot_is_high") is not True
    if rows[0]["trend"] == "BEARISH" and (
        rows[0].get("features") or {}
    ).get("last_confirmed_pivot_is_high") is True:
        assert rows[0]["action"] == "SELL"


@pytest.mark.asyncio
async def test_force_all_recomputes_whole_watchlist_board(
    monkeypatch: pytest.MonkeyPatch,
):
    """ALG-94 systemic: force_all refreshes every symbol, not only BUY+BEARISH."""
    from app import pine_refresh as pr

    closes: list[float] = []
    for i in range(30):
        closes.append(100.0 + i * 2.0)
    for i in range(20):
        closes.append(160.0 - i * 3.0)
    for i in range(25):
        closes.append(100.0 + i * 3.0)
    for i in range(30):
        closes.append(175.0 - i * 3.0)

    def _series(symbol: str) -> list[Candle]:
        return [_bar(i, close=c, symbol=symbol) for i, c in enumerate(closes)]

    async def _fake_candles(symbols):
        return {str(s).upper(): _series(str(s).upper()) for s in symbols}

    monkeypatch.setattr(pr, "persist_recomputed_pine_row", lambda row: None)
    monkeypatch.setattr("app.marks._live_15m_candles_map", _fake_candles)

    # Stale board: several BUY+BULLISH rows that old tip would skip (not BUY+BEARISH).
    rows = [
        {
            "symbol": "BAJFINANCE",
            "action": "BUY",
            "pine_action": "BUY",
            "trend": "BULLISH",
            "pine_persisted": True,
            "signal_engine": "reversal_pro_v32",
            "bar_ts": latest_closed_bar_open().isoformat(),
            "features": {
                "last_confirmed_pivot_is_high": False,  # stale U1 pivot claim
                "signal": 1,
                "buysignal": 1,
            },
        },
        {
            "symbol": "RELIANCE",
            "action": "BUY",
            "pine_action": "BUY",
            "trend": "BULLISH",
            "pine_persisted": True,
            "signal_engine": "reversal_pro_v32",
            "bar_ts": latest_closed_bar_open().isoformat(),
            "features": {
                "last_confirmed_pivot_is_high": False,
                "signal": 1,
                "buysignal": 1,
            },
        },
        {
            "symbol": "INFY",
            "action": "SELL",
            "pine_action": "SELL",
            "trend": "BEARISH",
            "pine_persisted": True,
            "signal_engine": "reversal_pro_v32",
            "bar_ts": latest_closed_bar_open().isoformat(),
            "features": {
                "last_confirmed_pivot_is_high": True,
                "signal": -1,
                "sellsignal": 1,
            },
        },
    ]
    # Without force_all, BUY+BULLISH+stale-pivot-low+current-bar looks "healthy"
    # and would blanket the board — force_all is what GET /signals uses.
    assert not pr.row_needs_candle_recompute(rows[0])
    assert not pr.row_needs_candle_recompute(rows[1])
    meta = await pr.refresh_rows_from_candles(rows, cfg={}, force_all=True)
    assert set(meta["recomputed"]) >= {"BAJFINANCE", "RELIANCE", "INFY"}
    for row in rows:
        pr.enforce_bearish_pivot_invariant([row])
        pivot = (row.get("features") or {}).get("last_confirmed_pivot_is_high")
        if pivot is True:
            assert row["action"] == "SELL", row["symbol"]
        if pivot is False:
            assert row["action"] == "BUY", row["symbol"]


def test_candle_miss_fallback_forces_sell_on_pivot_high():
    """Empty-candle GET path: pivot high → SELL; Trend NEUTRAL when unverified."""
    from app.pine_refresh import apply_candle_miss_fallback, enforce_bearish_pivot_invariant

    row = {
        "symbol": "BAJFINANCE",
        "action": "BUY",
        "pine_action": "BUY",
        "display_action": "BUY",
        "trend": "BULLISH",
        "pine_persisted": True,
        "candle_source": "synthetic",
        "bar_ts": "2026-11-20T04:00:00+00:00",  # stale
        "features": {
            "last_confirmed_pivot_is_high": True,
            "signal": 1,
            "buysignal": 1,
            "candle_source": "synthetic",
        },
    }
    apply_candle_miss_fallback(row)
    enforce_bearish_pivot_invariant([row])
    assert row["action"] == "SELL"
    assert row["trend"] == "NEUTRAL"
    assert row.get("pine_recompute_failed") is True


def test_candle_miss_fallback_refuses_synthetic_buy_without_pivot():
    from app.pine_refresh import apply_candle_miss_fallback

    row = {
        "symbol": "BAJFINANCE",
        "action": "BUY",
        "pine_action": "BUY",
        "trend": "BULLISH",
        "candle_source": "synthetic",
        "features": {"signal": 1, "buysignal": 1, "candle_source": "synthetic"},
    }
    apply_candle_miss_fallback(row)
    # Seed for BAJFINANCE is SELL, or refuse_unverified_buy → SELL.
    assert row["action"] == "SELL"
    assert row["trend"] == "NEUTRAL"


@pytest.mark.asyncio
async def test_refresh_clears_kv_when_candles_empty(monkeypatch: pytest.MonkeyPatch):
    from app import pine_refresh as pr

    async def _empty(_symbols):
        return {}

    cleared: list[str] = []

    def _del(sym: str) -> bool:
        cleared.append(sym)
        return True

    monkeypatch.setattr("app.marks._live_15m_candles_map", _empty)
    monkeypatch.setattr(pr, "_delete_pine_kv", _del)

    rows = [
        {
            "symbol": "BAJFINANCE",
            "action": "BUY",
            "pine_action": "BUY",
            "trend": "BULLISH",
            "pine_persisted": True,
            "features": {
                "last_confirmed_pivot_is_high": True,
                "signal": 1,
                "buysignal": 1,
            },
        }
    ]
    meta = await pr.refresh_rows_from_candles(rows, cfg={}, force_all=True)
    assert "BAJFINANCE" in meta["candle_miss_cleared"]
    assert "BAJFINANCE" in cleared
    assert rows[0]["action"] == "SELL"
    assert rows[0]["trend"] == "NEUTRAL"
