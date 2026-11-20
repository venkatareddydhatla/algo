"""Unit tests for Pine EMA cross + strict candle-clear confirm signals."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from options_portal_domain.pine_signals import (
    PineParams,
    _ema_series,
    _sma_series,
    atr_rma_series,
    dmi_adx_series,
    evaluate_pine_bar,
    evaluate_pine_signal,
    floor_to_timeframe,
    latest_closed_bar_open,
    pine_params_from_config,
    pine_signal_bar_is_current,
    pine_signals_enabled,
    prefer_closed_candles,
    supertrend_series,
    watchlist_pine_action,
)
from options_portal_domain.types import Candle, SignalAction


def _bar(
    i: int,
    *,
    close: float,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
    volume: int = 100_000,
    symbol: str = "RELIANCE",
) -> Candle:
    ts0 = datetime(2026, 11, 14, 3, 45, tzinfo=timezone.utc)
    c = float(close)
    h = float(high if high is not None else c * 1.002)
    l = float(low if low is not None else c * 0.998)
    o = float(open_ if open_ is not None else (h + l) / 2.0)
    return Candle(
        symbol=symbol,
        timeframe="15m",
        ts=ts0 + timedelta(minutes=15 * i),
        open=o,
        high=h,
        low=l,
        close=c,
        volume=int(volume),
    )


def test_pine_config_defaults_match_script_inputs():
    p = pine_params_from_config({})
    assert p.ema_fast == 13
    assert p.ema_slow == 21
    assert p.su_len == 10
    assert p.su_mult == pytest.approx(3.0)
    assert p.adx_len == 14
    assert pine_signals_enabled({}) is True
    assert pine_signals_enabled({"trading": {"pine_signals": {"enabled": False}}}) is False


def test_ema_series_sma_seed_then_ema():
    vals = [float(i) for i in range(1, 31)]
    ema = _ema_series(vals, 5)
    assert ema[3] is None
    assert ema[4] == pytest.approx(sum(vals[:5]) / 5)
    assert ema[-1] is not None
    assert ema[-1] > ema[10]  # type: ignore[operator]


def test_sma_volume_ratio_gate():
    vols = [100.0] * 20 + [200.0]
    sma = _sma_series(vols, 20)
    assert sma[-1] == pytest.approx(105.0)
    assert vols[-1] / sma[-1] > 1.2  # type: ignore[operator]


def test_supertrend_bull_on_sustained_uptrend():
    candles = [_bar(i, close=100 + i * 0.8, volume=120_000) for i in range(80)]
    atr_s, trend, _ = supertrend_series(candles, atr_period=10, multiplier=3.0)
    assert atr_s[-1] is not None
    assert trend[-1] == 1


def test_adx_produces_values_after_warmup():
    candles = [_bar(i, close=100 + (i % 7) * 0.5, volume=100_000) for i in range(60)]
    di_p, di_m, adx = dmi_adx_series(candles, di_length=14, adx_smoothing=14)
    assert any(v is not None for v in adx)
    last = next(v for v in reversed(adx) if v is not None)
    assert 0.0 <= float(last) <= 100.0
    assert di_p[-1] is not None or di_p[-2] is not None


def _find_crossover_index(
    candles: list[Candle], params: PineParams
) -> int | None:
    """Return first bar index where crossover is True on that bar."""
    for end in range(2, len(candles) + 1):
        st = evaluate_pine_bar(
            candles[:end], params=params, prefer_closed=False
        )
        assert st is not None
        if st.crossover:
            return end - 1
    return None


def _find_crossunder_index(
    candles: list[Candle], params: PineParams
) -> int | None:
    for end in range(2, len(candles) + 1):
        st = evaluate_pine_bar(
            candles[:end], params=params, prefer_closed=False
        )
        assert st is not None
        if st.crossunder:
            return end - 1
    return None


def test_prefer_closed_drops_forming_last_bar():
    candles = [_bar(i, close=100 + i) for i in range(5)]
    # Mid-bar: before last candle's 15m period ends.
    mid = candles[-1].ts + timedelta(minutes=5)
    closed = prefer_closed_candles(candles, now=mid)
    assert len(closed) == 4
    assert closed[-1].ts == candles[-2].ts
    # After bar end: keep all.
    after = candles[-1].ts + timedelta(minutes=15)
    assert len(prefer_closed_candles(candles, now=after)) == 5


def test_buy_does_not_fire_on_cross_bar():
    """Crossover bar itself must not emit buy_signal."""
    closes: list[float] = []
    px = 120.0
    for _ in range(20):
        px -= 1.0
        closes.append(px)
    for _ in range(15):
        px += 2.5
        closes.append(px)
    candles = [_bar(i, close=c) for i, c in enumerate(closes)]
    params = PineParams(ema_fast=2, ema_slow=3)
    cross_i = _find_crossover_index(candles, params)
    assert cross_i is not None
    st = evaluate_pine_bar(
        candles[: cross_i + 1], params=params, prefer_closed=False
    )
    assert st is not None
    assert st.crossover is True
    assert st.buy_signal is False
    assert st.prior_crossover is False


def test_buy_fires_when_next_candle_fully_above_both():
    """After crossover, next candle low > max(prior EMAs) → buy_signal."""
    closes: list[float] = []
    px = 120.0
    for _ in range(20):
        px -= 1.0
        closes.append(px)
    for _ in range(15):
        px += 2.5
        closes.append(px)
    base = [_bar(i, close=c) for i, c in enumerate(closes)]
    params = PineParams(ema_fast=2, ema_slow=3)
    cross_i = _find_crossover_index(base, params)
    assert cross_i is not None
    st_cross = evaluate_pine_bar(
        base[: cross_i + 1], params=params, prefer_closed=False
    )
    assert st_cross is not None
    assert st_cross.ema_fast is not None and st_cross.ema_slow is not None
    ef, es = float(st_cross.ema_fast), float(st_cross.ema_slow)
    both_hi = max(ef, es)

    # Entire candle clear above both EMAs (strict low > max).
    confirm = _bar(
        cross_i + 1,
        close=both_hi + 5.0,
        open_=both_hi + 3.0,
        high=both_hi + 6.0,
        low=both_hi + 2.0,
    )
    series = base[: cross_i + 1] + [confirm]
    st = evaluate_pine_bar(series, params=params, prefer_closed=False)
    assert st is not None
    assert st.prior_crossover is True
    assert st.crossover is False
    assert st.candle_clear_above is True
    assert st.buy_signal is True
    assert st.sell_signal is False

    result = evaluate_pine_signal(
        "RELIANCE", series, params=params, prefer_closed=False
    )
    assert result.action == SignalAction.BUY
    assert result.buy_signal is True
    assert "candle clear above both" in result.reason_summary


def test_buy_does_not_fire_when_wick_touches_ema():
    """Prior crossover but low sits on / below max EMA → no buy."""
    closes: list[float] = []
    px = 120.0
    for _ in range(20):
        px -= 1.0
        closes.append(px)
    for _ in range(15):
        px += 2.5
        closes.append(px)
    base = [_bar(i, close=c) for i, c in enumerate(closes)]
    params = PineParams(ema_fast=2, ema_slow=3)
    cross_i = _find_crossover_index(base, params)
    assert cross_i is not None
    st_cross = evaluate_pine_bar(
        base[: cross_i + 1], params=params, prefer_closed=False
    )
    assert st_cross is not None
    ef, es = float(st_cross.ema_fast), float(st_cross.ema_slow)  # type: ignore[arg-type]
    both_hi = max(ef, es)

    # Open/close above, but low touches the line → not clear.
    confirm = _bar(
        cross_i + 1,
        close=both_hi + 3.0,
        open_=both_hi + 2.0,
        high=both_hi + 4.0,
        low=both_hi,  # sits on max EMA
    )
    series = base[: cross_i + 1] + [confirm]
    st = evaluate_pine_bar(series, params=params, prefer_closed=False)
    assert st is not None
    assert st.prior_crossover is True
    assert st.candle_clear_above is False
    assert st.buy_signal is False


def test_buy_does_not_fire_when_candle_below_emas():
    """Prior crossover but candle fails clear → no buy."""
    closes: list[float] = []
    px = 120.0
    for _ in range(20):
        px -= 1.0
        closes.append(px)
    for _ in range(15):
        px += 2.5
        closes.append(px)
    base = [_bar(i, close=c) for i, c in enumerate(closes)]
    params = PineParams(ema_fast=2, ema_slow=3)
    cross_i = _find_crossover_index(base, params)
    assert cross_i is not None
    st_cross = evaluate_pine_bar(
        base[: cross_i + 1], params=params, prefer_closed=False
    )
    assert st_cross is not None
    ef, es = float(st_cross.ema_fast), float(st_cross.ema_slow)  # type: ignore[arg-type]
    both_lo = min(ef, es)

    confirm = _bar(
        cross_i + 1,
        close=both_lo - 1.0,
        open_=both_lo - 2.0,
        high=both_lo,
        low=both_lo - 3.0,
    )
    series = base[: cross_i + 1] + [confirm]
    st = evaluate_pine_bar(series, params=params, prefer_closed=False)
    assert st is not None
    assert st.prior_crossover is True
    assert st.candle_clear_above is False
    assert st.buy_signal is False


def test_sell_does_not_fire_on_cross_bar():
    closes: list[float] = []
    px = 80.0
    for _ in range(20):
        px += 1.0
        closes.append(px)
    for _ in range(15):
        px -= 2.5
        closes.append(px)
    candles = [_bar(i, close=c) for i, c in enumerate(closes)]
    params = PineParams(ema_fast=2, ema_slow=3)
    cross_i = _find_crossunder_index(candles, params)
    assert cross_i is not None
    st = evaluate_pine_bar(
        candles[: cross_i + 1], params=params, prefer_closed=False
    )
    assert st is not None
    assert st.crossunder is True
    assert st.sell_signal is False


def test_sell_fires_when_next_candle_fully_below_both():
    closes: list[float] = []
    px = 80.0
    for _ in range(20):
        px += 1.0
        closes.append(px)
    for _ in range(15):
        px -= 2.5
        closes.append(px)
    base = [_bar(i, close=c) for i, c in enumerate(closes)]
    params = PineParams(ema_fast=2, ema_slow=3)
    cross_i = _find_crossunder_index(base, params)
    assert cross_i is not None
    st_cross = evaluate_pine_bar(
        base[: cross_i + 1], params=params, prefer_closed=False
    )
    assert st_cross is not None
    ef, es = float(st_cross.ema_fast), float(st_cross.ema_slow)  # type: ignore[arg-type]
    both_lo = min(ef, es)

    confirm = _bar(
        cross_i + 1,
        close=both_lo - 5.0,
        open_=both_lo - 3.0,
        high=both_lo - 1.0,
        low=both_lo - 6.0,
    )
    series = base[: cross_i + 1] + [confirm]
    st = evaluate_pine_bar(series, params=params, prefer_closed=False)
    assert st is not None
    assert st.prior_crossunder is True
    assert st.candle_clear_below is True
    assert st.sell_signal is True
    assert st.buy_signal is False

    result = evaluate_pine_signal(
        "RELIANCE", series, params=params, prefer_closed=False
    )
    assert result.action == SignalAction.SELL
    assert "candle clear below both" in result.reason_summary


def test_sell_does_not_fire_when_wick_touches_ema():
    closes: list[float] = []
    px = 80.0
    for _ in range(20):
        px += 1.0
        closes.append(px)
    for _ in range(15):
        px -= 2.5
        closes.append(px)
    base = [_bar(i, close=c) for i, c in enumerate(closes)]
    params = PineParams(ema_fast=2, ema_slow=3)
    cross_i = _find_crossunder_index(base, params)
    assert cross_i is not None
    st_cross = evaluate_pine_bar(
        base[: cross_i + 1], params=params, prefer_closed=False
    )
    assert st_cross is not None
    ef, es = float(st_cross.ema_fast), float(st_cross.ema_slow)  # type: ignore[arg-type]
    both_lo = min(ef, es)

    # Body below, but high sits on min EMA → not clear.
    confirm = _bar(
        cross_i + 1,
        close=both_lo - 3.0,
        open_=both_lo - 2.0,
        high=both_lo,  # sits on min EMA
        low=both_lo - 4.0,
    )
    series = base[: cross_i + 1] + [confirm]
    st = evaluate_pine_bar(series, params=params, prefer_closed=False)
    assert st is not None
    assert st.prior_crossunder is True
    assert st.candle_clear_below is False
    assert st.sell_signal is False


def test_sell_does_not_fire_when_candle_above_emas():
    closes: list[float] = []
    px = 80.0
    for _ in range(20):
        px += 1.0
        closes.append(px)
    for _ in range(15):
        px -= 2.5
        closes.append(px)
    base = [_bar(i, close=c) for i, c in enumerate(closes)]
    params = PineParams(ema_fast=2, ema_slow=3)
    cross_i = _find_crossunder_index(base, params)
    assert cross_i is not None
    st_cross = evaluate_pine_bar(
        base[: cross_i + 1], params=params, prefer_closed=False
    )
    assert st_cross is not None
    ef, es = float(st_cross.ema_fast), float(st_cross.ema_slow)  # type: ignore[arg-type]
    both_hi = max(ef, es)

    confirm = _bar(
        cross_i + 1,
        close=both_hi + 1.0,
        open_=both_hi + 2.0,
        high=both_hi + 3.0,
        low=both_hi,
    )
    series = base[: cross_i + 1] + [confirm]
    st = evaluate_pine_bar(series, params=params, prefer_closed=False)
    assert st is not None
    assert st.prior_crossunder is True
    assert st.candle_clear_below is False
    assert st.sell_signal is False


def test_buy_sell_ignore_st_adx_vol():
    """Gate uses only prior cross + candle clear (ST/ADX/vol unused)."""
    closes: list[float] = []
    px = 120.0
    for _ in range(20):
        px -= 1.0
        closes.append(px)
    for _ in range(15):
        px += 2.5
        closes.append(px)
    base = [_bar(i, close=c, volume=1) for i, c in enumerate(closes)]  # dead vol
    params = PineParams(ema_fast=2, ema_slow=3, use_adx=True, vol_ratio=1.2)
    cross_i = _find_crossover_index(base, params)
    assert cross_i is not None
    st_cross = evaluate_pine_bar(
        base[: cross_i + 1], params=params, prefer_closed=False
    )
    assert st_cross is not None
    both_hi = max(float(st_cross.ema_fast), float(st_cross.ema_slow))  # type: ignore[arg-type]
    confirm = _bar(
        cross_i + 1,
        close=both_hi + 5.0,
        open_=both_hi + 3.0,
        high=both_hi + 6.0,
        low=both_hi + 2.0,
        volume=1,
    )
    st = evaluate_pine_bar(
        base[: cross_i + 1] + [confirm], params=params, prefer_closed=False
    )
    assert st is not None
    assert st.high_volume is False
    assert st.buy_signal is True
    assert st.buy_signal == (st.prior_crossover and st.candle_clear_above)


def test_evaluate_pine_signal_maps_actions():
    candles = [_bar(i, close=100 + i * 0.2, volume=150_000) for i in range(40)]
    result = evaluate_pine_signal("RELIANCE", candles, prefer_closed=False)
    assert result.action in {SignalAction.BUY, SignalAction.SELL, SignalAction.HOLD}
    assert result.symbol == "RELIANCE"
    if result.buy_signal:
        assert result.action == SignalAction.BUY
        assert result.score == 1.0
    elif result.sell_signal:
        assert result.action == SignalAction.SELL
        assert result.score == -1.0
    else:
        assert result.action == SignalAction.HOLD
        assert result.score == 0.0


def test_formula_shape_on_last_bar():
    candles = [_bar(i, close=100 + i * 0.05, volume=200_000) for i in range(50)]
    st = evaluate_pine_bar(candles, params=PineParams(), prefer_closed=False)
    assert st is not None
    assert st.buy_signal == (st.prior_crossover and st.candle_clear_above)
    assert st.sell_signal == (st.prior_crossunder and st.candle_clear_below)


def test_forming_bar_deferred_until_closed():
    """While confirm candle is still forming, evaluate prior closed bar only."""
    closes: list[float] = []
    px = 120.0
    for _ in range(20):
        px -= 1.0
        closes.append(px)
    for _ in range(15):
        px += 2.5
        closes.append(px)
    base = [_bar(i, close=c) for i, c in enumerate(closes)]
    params = PineParams(ema_fast=2, ema_slow=3)
    cross_i = _find_crossover_index(base, params)
    assert cross_i is not None
    st_cross = evaluate_pine_bar(
        base[: cross_i + 1], params=params, prefer_closed=False
    )
    assert st_cross is not None
    both_hi = max(float(st_cross.ema_fast), float(st_cross.ema_slow))  # type: ignore[arg-type]
    confirm = _bar(
        cross_i + 1,
        close=both_hi + 5.0,
        open_=both_hi + 3.0,
        high=both_hi + 6.0,
        low=both_hi + 2.0,
    )
    series = base[: cross_i + 1] + [confirm]
    mid = confirm.ts + timedelta(minutes=5)
    st_mid = evaluate_pine_bar(series, params=params, now=mid, prefer_closed=True)
    assert st_mid is not None
    # Still on cross bar → no buy yet.
    assert st_mid.crossover is True
    assert st_mid.buy_signal is False

    after = confirm.ts + timedelta(minutes=15)
    st_closed = evaluate_pine_bar(
        series, params=params, now=after, prefer_closed=True
    )
    assert st_closed is not None
    assert st_closed.prior_crossover is True
    assert st_closed.candle_clear_above is True
    assert st_closed.buy_signal is True


def test_atr_rma_warmup():
    candles = [_bar(i, close=100 + i * 0.1) for i in range(25)]
    atr = atr_rma_series(candles, 10)
    assert all(v is None for v in atr[:9])
    assert atr[9] is not None
    assert atr[-1] is not None


def test_pine_signal_bar_current_same_15m_and_latest_closed():
    """BUY/SELL live only on signal bar / same 15m (or while it is latest closed)."""
    bar = datetime(2026, 11, 20, 4, 0, tzinfo=timezone.utc)  # 09:30 IST
    # During the signal bar bucket.
    assert pine_signal_bar_is_current(bar, now=bar + timedelta(minutes=7)) is True
    assert watchlist_pine_action("SELL", bar, now=bar + timedelta(minutes=7)) == "SELL"
    # Next 15m: signal bar is still the latest closed under prefer_closed.
    mid_next = bar + timedelta(minutes=20)  # 04:20 UTC
    assert floor_to_timeframe(mid_next) == bar + timedelta(minutes=15)
    assert latest_closed_bar_open(mid_next) == bar
    assert pine_signal_bar_is_current(bar, now=mid_next) is True
    assert watchlist_pine_action("SELL", bar, now=mid_next) == "SELL"
    # Hours later (APOLLOHOSP case) → HOLD.
    hours_later = bar + timedelta(hours=5)
    assert pine_signal_bar_is_current(bar, now=hours_later) is False
    assert watchlist_pine_action("SELL", bar, now=hours_later) == "HOLD"
    # Missing bar_ts → not current (stale KV without stamp).
    assert pine_signal_bar_is_current(None, now=hours_later) is False
    assert watchlist_pine_action("BUY", None, now=mid_next) == "HOLD"
    assert watchlist_pine_action("HOLD", bar, now=hours_later) == "HOLD"
