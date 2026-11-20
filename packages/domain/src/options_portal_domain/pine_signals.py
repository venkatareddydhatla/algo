"""NIFTY50ActiveV2 Pine Script signal engine (exact buy/sell formulas).

Canonical script: docs/pine-nifty50-active-v2.pine (Project store).

Defaults match Pine inputs:
- Fast EMA 13, Slow EMA 21
- Supertrend ATR len 10, mult 3.0 (custom recursive bands — not ta.supertrend)
- ADX/DI length 14; adx_ok = adx > 20 when use_adx
- Vol ratio 1.2: volume / sma(volume, 20) > 1.2

buy_signal  = crossover(fastEma, slowEma) and supertrendBull and high_volume and adx_ok
sell_signal = crossunder(fastEma, slowEma) and high_volume and adx_ok
(sell does NOT require supertrend bear.)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from options_portal_domain.types import Candle, SignalAction

# Pine input defaults
DEFAULT_EMA_FAST = 13
DEFAULT_EMA_SLOW = 21
DEFAULT_SU_LEN = 10
DEFAULT_SU_MULT = 3.0
DEFAULT_ADX_LEN = 14
DEFAULT_USE_ADX = True
DEFAULT_ADX_MIN = 20.0
DEFAULT_VOL_RATIO = 1.2
DEFAULT_VOL_SMA_LEN = 20


@dataclass(frozen=True)
class PineParams:
    ema_fast: int = DEFAULT_EMA_FAST
    ema_slow: int = DEFAULT_EMA_SLOW
    su_len: int = DEFAULT_SU_LEN
    su_mult: float = DEFAULT_SU_MULT
    adx_len: int = DEFAULT_ADX_LEN
    use_adx: bool = DEFAULT_USE_ADX
    adx_min: float = DEFAULT_ADX_MIN
    vol_ratio: float = DEFAULT_VOL_RATIO
    vol_sma_len: int = DEFAULT_VOL_SMA_LEN


@dataclass(frozen=True)
class PineBarState:
    """Per-bar Pine feature snapshot (last bar is the decision bar)."""

    ema_fast: float | None
    ema_slow: float | None
    atr: float | None
    trend: int  # 1 bull, -1 bear
    supertrend_bull: bool
    di_plus: float | None
    di_minus: float | None
    adx: float | None
    adx_ok: bool
    volume_ma: float | None
    volume_ratio: float | None
    high_volume: bool
    crossover: bool
    crossunder: bool
    buy_signal: bool
    sell_signal: bool


@dataclass(frozen=True)
class PineSignalResult:
    """Equity dry-run action derived solely from Pine buy/sell signals."""

    symbol: str
    action: SignalAction  # BUY | SELL | HOLD
    buy_signal: bool
    sell_signal: bool
    score: float  # +1 buy, -1 sell, 0 hold
    confidence: float
    reasons: list[str]
    features: dict[str, Any] = field(default_factory=dict)
    state: PineBarState | None = None

    @property
    def reason_summary(self) -> str:
        if self.buy_signal:
            return "EMA cross+ST+ADX+vol"
        if self.sell_signal:
            return "EMA crossunder+ADX+vol"
        return "no Pine signal"

    @property
    def reason_detail(self) -> str:
        if not self.reasons:
            return "Neutral: no Pine buy/sell on last bar"
        body = "\n".join(f"- {r}" for r in self.reasons)
        return f"Technical:\n{body}"


def pine_params_from_config(cfg: Mapping[str, Any] | None) -> PineParams:
    """Read ``trading.pine_signals`` (or ``ta.pine``) with Pine defaults."""
    root = dict(cfg or {})
    trading = dict(root.get("trading") or {})
    raw = dict(trading.get("pine_signals") or root.get("pine_signals") or {})
    if not raw:
        ta = dict(root.get("ta") or {})
        raw = dict(ta.get("pine") or {})
    return PineParams(
        ema_fast=int(raw.get("ema_fast", DEFAULT_EMA_FAST)),
        ema_slow=int(raw.get("ema_slow", DEFAULT_EMA_SLOW)),
        su_len=int(raw.get("su_len", raw.get("atr_period", DEFAULT_SU_LEN))),
        su_mult=float(raw.get("su_mult", raw.get("multiplier", DEFAULT_SU_MULT))),
        adx_len=int(raw.get("adx_len", DEFAULT_ADX_LEN)),
        use_adx=bool(raw.get("use_adx", DEFAULT_USE_ADX)),
        adx_min=float(raw.get("adx_min", DEFAULT_ADX_MIN)),
        vol_ratio=float(raw.get("vol_ratio", DEFAULT_VOL_RATIO)),
        vol_sma_len=int(raw.get("vol_sma_len", DEFAULT_VOL_SMA_LEN)),
    )


def pine_signals_enabled(cfg: Mapping[str, Any] | None) -> bool:
    """Pine engine is the primary equity dry-run path when enabled (default True)."""
    root = dict(cfg or {})
    trading = dict(root.get("trading") or {})
    raw = dict(trading.get("pine_signals") or root.get("pine_signals") or {})
    return bool(raw.get("enabled", True))


def _ema_series(values: Sequence[float], period: int) -> list[float | None]:
    """TradingView-style EMA: SMA seed over first ``period`` bars."""
    n = len(values)
    out: list[float | None] = [None] * n
    if period <= 0 or n < period:
        return out
    k = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    ema = seed
    for i in range(period, n):
        ema = values[i] * k + ema * (1.0 - k)
        out[i] = ema
    return out


def _rma_series(values: Sequence[float], period: int) -> list[float | None]:
    """Wilder RMA (TradingView ``ta.rma``) — SMA seed then alpha=1/period."""
    n = len(values)
    out: list[float | None] = [None] * n
    if period <= 0 or n < period:
        return out
    alpha = 1.0 / period
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    rma = seed
    for i in range(period, n):
        rma = alpha * values[i] + (1.0 - alpha) * rma
        out[i] = rma
    return out


def _sma_series(values: Sequence[float], period: int) -> list[float | None]:
    n = len(values)
    out: list[float | None] = [None] * n
    if period <= 0 or n < period:
        return out
    window = 0.0
    for i, v in enumerate(values):
        window += v
        if i >= period:
            window -= values[i - period]
        if i >= period - 1:
            out[i] = window / period
    return out


def _true_ranges(candles: Sequence[Candle]) -> list[float]:
    """TR series; index 0 uses high-low (no prior close)."""
    trs: list[float] = []
    for i, c in enumerate(candles):
        if i == 0:
            trs.append(float(c.high) - float(c.low))
        else:
            prev_c = float(candles[i - 1].close)
            trs.append(
                max(
                    float(c.high) - float(c.low),
                    abs(float(c.high) - prev_c),
                    abs(float(c.low) - prev_c),
                )
            )
    return trs


def atr_rma_series(candles: Sequence[Candle], period: int) -> list[float | None]:
    """TradingView ``ta.atr(period)`` = RMA of true range."""
    return _rma_series(_true_ranges(candles), period)


def supertrend_series(
    candles: Sequence[Candle],
    *,
    atr_period: int = DEFAULT_SU_LEN,
    multiplier: float = DEFAULT_SU_MULT,
) -> tuple[list[float | None], list[int], list[float | None]]:
    """Custom Supertrend bands matching the Pine script (not ta.supertrend).

    Returns ``(atr_series, trend_series, trend_line)`` where trend is +1 / -1
    and ``trend_line`` is the active band (dn when bull, up when bear).
    """
    n = len(candles)
    atr_s = atr_rma_series(candles, atr_period)
    trend: list[int] = [1] * n
    trend_up: list[float | None] = [None] * n
    trend_down: list[float | None] = [None] * n
    line: list[float | None] = [None] * n

    for i in range(n):
        atr_v = atr_s[i]
        if atr_v is None:
            trend[i] = trend[i - 1] if i else 1
            continue
        mid = (float(candles[i].high) + float(candles[i].low)) / 2.0
        up_ = mid + float(multiplier) * float(atr_v)
        dn_ = mid - float(multiplier) * float(atr_v)

        if i == 0 or trend_up[i - 1] is None:
            trend_up[i] = up_
        else:
            prev_up = float(trend_up[i - 1])  # type: ignore[arg-type]
            prev_close = float(candles[i - 1].close)
            # Pine: up_ > trendUp[1] or close[1] > trendUp[1] ? up_ : trendUp[1]
            if up_ > prev_up or prev_close > prev_up:
                trend_up[i] = up_
            else:
                trend_up[i] = prev_up

        if i == 0 or trend_down[i - 1] is None:
            trend_down[i] = dn_
        else:
            prev_dn = float(trend_down[i - 1])  # type: ignore[arg-type]
            prev_close = float(candles[i - 1].close)
            # Pine: dn_ < trendDown[1] or close[1] < trendDown[1] ? dn_ : trendDown[1]
            if dn_ < prev_dn or prev_close < prev_dn:
                trend_down[i] = dn_
            else:
                trend_down[i] = prev_dn

        if i == 0:
            trend[i] = 1
        else:
            prev_trend = trend[i - 1]
            close = float(candles[i].close)
            prev_td = trend_down[i - 1]
            prev_tu = trend_up[i - 1]
            # Pine: close > trendDown[1] ? 1 : close < trendUp[1] ? -1 : trend[1]
            if prev_td is not None and close > float(prev_td):
                trend[i] = 1
            elif prev_tu is not None and close < float(prev_tu):
                trend[i] = -1
            else:
                trend[i] = prev_trend

        line[i] = trend_down[i] if trend[i] == 1 else trend_up[i]

    return atr_s, trend, line


def dmi_adx_series(
    candles: Sequence[Candle],
    *,
    di_length: int = DEFAULT_ADX_LEN,
    adx_smoothing: int | None = None,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """TradingView ``ta.dmi(diLength, adxSmoothing)`` → (di+, di−, adx)."""
    n = len(candles)
    smooth = int(adx_smoothing if adx_smoothing is not None else di_length)
    empty: list[float | None] = [None] * n
    if n < 2 or di_length <= 0:
        return empty, empty, empty

    plus_dm: list[float] = [0.0] * n
    minus_dm: list[float] = [0.0] * n
    tr: list[float] = _true_ranges(candles)

    for i in range(1, n):
        up_move = float(candles[i].high) - float(candles[i - 1].high)
        down_move = float(candles[i - 1].low) - float(candles[i].low)
        plus_dm[i] = up_move if up_move > down_move and up_move > 0 else 0.0
        minus_dm[i] = down_move if down_move > up_move and down_move > 0 else 0.0

    # TV seeds RMA from bar 0; TR[0] is high-low. DM[0]=0.
    atr_s = _rma_series(tr, di_length)
    plus_s = _rma_series(plus_dm, di_length)
    minus_s = _rma_series(minus_dm, di_length)

    di_plus: list[float | None] = [None] * n
    di_minus: list[float | None] = [None] * n
    dx: list[float] = [0.0] * n
    dx_ready: list[bool] = [False] * n

    for i in range(n):
        a = atr_s[i]
        p = plus_s[i]
        m = minus_s[i]
        if a is None or p is None or m is None or float(a) <= 0:
            continue
        dp = 100.0 * float(p) / float(a)
        dm = 100.0 * float(m) / float(a)
        di_plus[i] = dp
        di_minus[i] = dm
        denom = dp + dm
        if denom > 0:
            dx[i] = 100.0 * abs(dp - dm) / denom
            dx_ready[i] = True

    # ADX = RMA of DX; only feed values once DX is defined (after di_length).
    # Match TV: rma(dx, adx_smoothing) over the full series with 0s before ready
    # produces a slight bias; TV uses na until DX exists. We compute RMA only
    # on the suffix starting at first ready DX index.
    adx: list[float | None] = [None] * n
    first = next((i for i, ok in enumerate(dx_ready) if ok), None)
    if first is not None:
        dx_vals = [dx[i] if dx_ready[i] else 0.0 for i in range(first, n)]
        adx_suf = _rma_series(dx_vals, smooth)
        for j, val in enumerate(adx_suf):
            adx[first + j] = val

    return di_plus, di_minus, adx


def evaluate_pine_bar(
    candles: Sequence[Candle],
    *,
    params: PineParams | None = None,
) -> PineBarState | None:
    """Compute Pine features + buy/sell on the last bar. None if empty."""
    if not candles:
        return None
    p = params or PineParams()
    closes = [float(c.close) for c in candles]
    volumes = [float(c.volume) for c in candles]
    n = len(candles)
    i = n - 1

    ema_fast_s = _ema_series(closes, int(p.ema_fast))
    ema_slow_s = _ema_series(closes, int(p.ema_slow))
    atr_s, trend_s, _line = supertrend_series(
        candles, atr_period=int(p.su_len), multiplier=float(p.su_mult)
    )
    di_p, di_m, adx_s = dmi_adx_series(
        candles, di_length=int(p.adx_len), adx_smoothing=int(p.adx_len)
    )
    vol_ma_s = _sma_series(volumes, int(p.vol_sma_len))

    ef = ema_fast_s[i]
    es = ema_slow_s[i]
    crossover = False
    crossunder = False
    if i >= 1 and ef is not None and es is not None:
        ef_prev = ema_fast_s[i - 1]
        es_prev = ema_slow_s[i - 1]
        if ef_prev is not None and es_prev is not None:
            # ta.crossover / ta.crossunder
            crossover = ef_prev <= es_prev and ef > es
            crossunder = ef_prev >= es_prev and ef < es

    trend = int(trend_s[i]) if trend_s else 1
    supertrend_bull = trend == 1
    adx_v = adx_s[i]
    if p.use_adx:
        adx_ok = adx_v is not None and float(adx_v) > float(p.adx_min)
    else:
        adx_ok = True

    vol_ma = vol_ma_s[i]
    if vol_ma is not None and float(vol_ma) > 0:
        volume_ratio = volumes[i] / float(vol_ma)
    else:
        volume_ratio = None
    high_volume = volume_ratio is not None and float(volume_ratio) > float(p.vol_ratio)

    buy_signal = bool(crossover and supertrend_bull and high_volume and adx_ok)
    sell_signal = bool(crossunder and high_volume and adx_ok)

    return PineBarState(
        ema_fast=ef,
        ema_slow=es,
        atr=atr_s[i],
        trend=trend,
        supertrend_bull=supertrend_bull,
        di_plus=di_p[i],
        di_minus=di_m[i],
        adx=adx_v,
        adx_ok=adx_ok,
        volume_ma=vol_ma,
        volume_ratio=volume_ratio,
        high_volume=high_volume,
        crossover=crossover,
        crossunder=crossunder,
        buy_signal=buy_signal,
        sell_signal=sell_signal,
    )


def evaluate_pine_signal(
    symbol: str,
    candles: Sequence[Candle],
    *,
    params: PineParams | None = None,
    cfg: Mapping[str, Any] | None = None,
) -> PineSignalResult:
    """Map last-bar Pine buy/sell → BUY / SELL / HOLD with Pine-only reasons."""
    p = params or pine_params_from_config(cfg)
    state = evaluate_pine_bar(candles, params=p)
    sym = str(symbol or "").upper()
    if state is None:
        return PineSignalResult(
            symbol=sym,
            action=SignalAction.HOLD,
            buy_signal=False,
            sell_signal=False,
            score=0.0,
            confidence=0.0,
            reasons=["no candles"],
            features={},
            state=None,
        )

    reasons: list[str] = []
    if state.ema_fast is not None and state.ema_slow is not None:
        reasons.append(
            f"EMA{p.ema_fast}={state.ema_fast:.4f} EMA{p.ema_slow}={state.ema_slow:.4f}"
        )
    if state.crossover:
        reasons.append(f"EMA{p.ema_fast} crossover EMA{p.ema_slow}")
    if state.crossunder:
        reasons.append(f"EMA{p.ema_fast} crossunder EMA{p.ema_slow}")
    reasons.append(
        "Supertrend bullish" if state.supertrend_bull else "Supertrend bearish"
    )
    if state.adx is not None:
        reasons.append(
            f"ADX({p.adx_len})={state.adx:.2f} "
            f"{'ok' if state.adx_ok else f'need>{p.adx_min}'}"
        )
    elif p.use_adx:
        reasons.append(f"ADX({p.adx_len}) unavailable")
    if state.volume_ratio is not None:
        reasons.append(
            f"vol/sma{p.vol_sma_len}={state.volume_ratio:.2f} "
            f"{'ok' if state.high_volume else f'need>{p.vol_ratio}'}"
        )
    else:
        reasons.append("volume ratio unavailable")

    if state.buy_signal:
        action = SignalAction.BUY
        score = 1.0
        reasons.append("buy_signal=True")
    elif state.sell_signal:
        action = SignalAction.SELL
        score = -1.0
        reasons.append("sell_signal=True")
    else:
        action = SignalAction.HOLD
        score = 0.0
        reasons.append("buy_signal=False sell_signal=False → HOLD")

    features = {
        "ema_fast": state.ema_fast,
        "ema_slow": state.ema_slow,
        "supertrend_dir": 1.0 if state.supertrend_bull else -1.0,
        "supertrend_bull": state.supertrend_bull,
        "atr": state.atr,
        "adx": state.adx,
        "di_plus": state.di_plus,
        "di_minus": state.di_minus,
        "adx_ok": state.adx_ok,
        "volume_ratio": state.volume_ratio,
        "high_volume": state.high_volume,
        "crossover": state.crossover,
        "crossunder": state.crossunder,
        "buy_signal": state.buy_signal,
        "sell_signal": state.sell_signal,
        "pine": True,
    }
    conf = 0.0
    if state.buy_signal or state.sell_signal:
        conf = 0.85
        if state.adx is not None:
            conf = min(1.0, 0.7 + min(0.3, float(state.adx) / 100.0))

    return PineSignalResult(
        symbol=sym,
        action=action,
        buy_signal=state.buy_signal,
        sell_signal=state.sell_signal,
        score=score,
        confidence=round(conf, 4),
        reasons=reasons,
        features=features,
        state=state,
    )
