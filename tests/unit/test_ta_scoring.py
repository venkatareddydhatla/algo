"""Unit tests for TA indicator scoring + confluence signal generator."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from options_portal_domain.types import Candle, SignalAction
from options_portal_ta.indicators import EMAPlugin, RSIPlugin, SupertrendPlugin, VWAPPlugin
from options_portal_ta.registry import IndicatorRegistry
from options_portal_ta.signal_generator import ConfluenceSignalGenerator


def _candles(n: int = 40, start: float = 100.0, drift: float = 0.15) -> list[Candle]:
    ts0 = datetime(2026, 11, 14, 3, 45, tzinfo=timezone.utc)  # ~09:15 IST
    out: list[Candle] = []
    price = start
    for i in range(n):
        o = price
        c = price * (1 + drift / 100)
        h = max(o, c) * 1.002
        l = min(o, c) * 0.998
        out.append(
            Candle(
                symbol="NIFTY",
                timeframe="15m",
                ts=ts0 + timedelta(minutes=15 * i),
                open=o,
                high=h,
                low=l,
                close=c,
                volume=100_000 + i * 500,
            )
        )
        price = c
    return out


def test_rsi_plugin_returns_bounded_value():
    candles = _candles()
    features = RSIPlugin(period=14).compute(candles)
    assert features["rsi"] is not None
    assert 0 <= features["rsi"] <= 100


def test_registry_merges_indicator_features():
    reg = IndicatorRegistry()
    reg.register(RSIPlugin())
    reg.register(EMAPlugin())
    reg.register(SupertrendPlugin())
    reg.register(VWAPPlugin())
    features = reg.compute_all(_candles())
    assert "rsi" in features
    assert "ema_cross" in features
    assert "supertrend_dir" in features
    assert "vwap" in features


def test_confluence_bullish_uptrend_prefers_ce_bias():
    candles = _candles(drift=0.25)
    reg = IndicatorRegistry()
    reg.register(RSIPlugin())
    reg.register(EMAPlugin())
    reg.register(SupertrendPlugin())
    reg.register(VWAPPlugin())
    features = reg.compute_all(candles)
    signal = ConfluenceSignalGenerator().evaluate("NIFTY", candles, features)
    assert signal.symbol == "NIFTY"
    assert -1.0 <= signal.score <= 1.0
    assert 0.0 <= signal.confidence <= 1.0
    assert signal.action in {
        SignalAction.BUY_CE_BIAS,
        SignalAction.HOLD,
        SignalAction.BUY_PE_BIAS,
    }
    # Strong uptrend should not be PE-biased
    assert signal.action != SignalAction.BUY_PE_BIAS
    assert signal.score > 0


def test_confluence_downtrend_prefers_pe_or_hold():
    candles = _candles(drift=-0.25)
    reg = IndicatorRegistry()
    reg.register(RSIPlugin())
    reg.register(EMAPlugin())
    reg.register(SupertrendPlugin())
    reg.register(VWAPPlugin())
    features = reg.compute_all(candles)
    signal = ConfluenceSignalGenerator().evaluate("BANKNIFTY", candles, features)
    assert signal.score < 0
    assert signal.action != SignalAction.BUY_CE_BIAS
