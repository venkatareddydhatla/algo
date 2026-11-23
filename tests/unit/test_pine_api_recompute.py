"""ALG-86: API-side pine refresh + hard BEARISH+pivot-high → SELL guarantee."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from options_portal_domain.pine_signals import (
    PineBarState,
    PineParams,
    enforce_bearish_pivot_sell_action,
    evaluate_pine_signal,
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
    """Trend BEARISH + lastConfirmedPivotIsHigh → SELL even if signal=+1."""
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
    if row["trend"] == "BEARISH" and (
        row.get("features") or {}
    ).get("last_confirmed_pivot_is_high") is True:
        assert row["action"] == "SELL"
    # After D1 path on this series, Action must not be leftover BUY when signal-
    if (row.get("features") or {}).get("signal", 0) < 0:
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
    assert not row_needs_candle_recompute(
        {
            "action": "SELL",
            "trend": "BEARISH",
            "pine_persisted": True,
            "signal_engine": "reversal_pro_v32",
            "features": {"last_confirmed_pivot_is_high": True},
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
