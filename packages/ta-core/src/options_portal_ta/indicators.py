"""Indicator plugins — pure functions on OHLCV."""

from __future__ import annotations

from collections.abc import Sequence

from options_portal_domain.types import Candle


def _closes(candles: Sequence[Candle]) -> list[float]:
    return [c.close for c in candles]


def _ema(values: Sequence[float], period: int) -> float | None:
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
    return ema


def _rsi(values: Sequence[float], period: int = 14) -> float | None:
    if len(values) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(values)):
        delta = values[i] - values[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _atr(candles: Sequence[Candle], period: int) -> float | None:
    if len(candles) < period + 1:
        return None
    trs: list[float] = []
    for i in range(1, len(candles)):
        high, low, prev_close = candles[i].high, candles[i].low, candles[i - 1].close
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    return sum(trs[-period:]) / period


class RSIPlugin:
    name = "rsi"
    version = "1.0.0"

    def __init__(self, period: int = 14) -> None:
        self.period = period

    def compute(self, candles: Sequence[Candle]) -> dict[str, float | None]:
        return {"rsi": _rsi(_closes(candles), self.period)}


class EMAPlugin:
    name = "ema_cross"
    version = "1.0.0"

    def __init__(self, fast: int = 9, slow: int = 21) -> None:
        self.fast = fast
        self.slow = slow

    def compute(self, candles: Sequence[Candle]) -> dict[str, float | None]:
        closes = _closes(candles)
        fast = _ema(closes, self.fast)
        slow = _ema(closes, self.slow)
        cross = None
        if fast is not None and slow is not None:
            cross = 1.0 if fast > slow else -1.0
        return {"ema_fast": fast, "ema_slow": slow, "ema_cross": cross}


class SupertrendPlugin:
    name = "supertrend"
    version = "1.0.0"

    def __init__(self, atr_period: int = 10, multiplier: float = 3.0) -> None:
        self.atr_period = atr_period
        self.multiplier = multiplier

    def compute(self, candles: Sequence[Candle]) -> dict[str, float | None]:
        if len(candles) < self.atr_period + 1:
            return {"supertrend": None, "supertrend_dir": None}
        atr = _atr(candles, self.atr_period)
        if atr is None:
            return {"supertrend": None, "supertrend_dir": None}
        last = candles[-1]
        mid = (last.high + last.low) / 2
        upper = mid + self.multiplier * atr
        lower = mid - self.multiplier * atr
        # Simplified direction: close vs midline band
        direction = 1.0 if last.close >= mid else -1.0
        st = lower if direction > 0 else upper
        return {"supertrend": st, "supertrend_dir": direction, "atr": atr}


class VWAPPlugin:
    name = "vwap"
    version = "1.0.0"

    def __init__(self, session_anchored: bool = True) -> None:
        self.session_anchored = session_anchored

    def compute(self, candles: Sequence[Candle]) -> dict[str, float | None]:
        if not candles:
            return {"vwap": None, "vwap_bias": None}
        # Session-anchored approximation: cumulative typical price * volume
        cum_pv = 0.0
        cum_v = 0.0
        for c in candles:
            typical = (c.high + c.low + c.close) / 3
            vol = max(c.volume, 1)
            cum_pv += typical * vol
            cum_v += vol
        vwap = cum_pv / cum_v if cum_v else None
        bias = None
        if vwap is not None:
            bias = 1.0 if candles[-1].close >= vwap else -1.0
        return {"vwap": vwap, "vwap_bias": bias}
