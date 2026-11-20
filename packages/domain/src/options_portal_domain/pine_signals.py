"""Pine equity signal engine — EMA cross + strict candle-clear confirm.

On 15m bars; green = fast EMA(13), red = slow EMA(21).

buy_signal  = prior-bar crossover(fastEma, slowEma)
              AND low > max(fastEma[1], slowEma[1])
sell_signal = prior-bar crossunder(fastEma, slowEma)
              AND high < min(fastEma[1], slowEma[1])

Signals do NOT fire on the cross bar; they fire on the next completed bar
when that candle sits fully above (buy) or below (sell) both EMAs from the
closed cross bar. Touching or sitting on the lines does not confirm.

Prefer closed bars: a still-forming last candle is dropped before evaluation
so mid-bar wicks cannot trigger.

Supertrend / ADX / volume are computed for diagnostics only and are NOT
part of the buy/sell gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from options_portal_domain.types import Candle, SignalAction

# Pine input defaults (EMA lengths drive the signal; ST/ADX/vol kept for config compat)
DEFAULT_EMA_FAST = 13
DEFAULT_EMA_SLOW = 21
DEFAULT_SU_LEN = 10
DEFAULT_SU_MULT = 3.0
DEFAULT_ADX_LEN = 14
DEFAULT_USE_ADX = True
DEFAULT_ADX_MIN = 20.0
DEFAULT_VOL_RATIO = 1.2
DEFAULT_VOL_SMA_LEN = 20
DEFAULT_TIMEFRAME_MINUTES = 15


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
    crossover: bool  # EMA cross on this (last) bar
    crossunder: bool
    prior_crossover: bool  # EMA cross on previous bar
    prior_crossunder: bool
    candle_clear_above: bool  # low > max(prior EMAs)
    candle_clear_below: bool  # high < min(prior EMAs)
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
            return "EMA cross + candle clear above both"
        if self.sell_signal:
            return "EMA crossunder + candle clear below both"
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


def prefer_closed_candles(
    candles: Sequence[Candle],
    *,
    timeframe_minutes: int = DEFAULT_TIMEFRAME_MINUTES,
    now: datetime | None = None,
) -> list[Candle]:
    """Drop a still-forming last bar so signals use completed candles only."""
    if not candles:
        return []
    series = list(candles)
    if len(series) < 2:
        return series
    last = series[-1]
    ts = last.ts
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    clock = now or datetime.now(tz=timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    bar_end = ts + timedelta(minutes=int(timeframe_minutes))
    if clock < bar_end:
        return series[:-1]
    return series


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
    """Custom Supertrend bands (diagnostics only; not part of buy/sell gate).

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
    """TradingView ``ta.dmi(diLength, adxSmoothing)`` → (di+, di−, adx). Diagnostics only."""
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
    adx: list[float | None] = [None] * n
    first = next((i for i, ok in enumerate(dx_ready) if ok), None)
    if first is not None:
        dx_vals = [dx[i] if dx_ready[i] else 0.0 for i in range(first, n)]
        adx_suf = _rma_series(dx_vals, smooth)
        for j, val in enumerate(adx_suf):
            adx[first + j] = val

    return di_plus, di_minus, adx


def _cross_at(
    ema_fast_s: Sequence[float | None],
    ema_slow_s: Sequence[float | None],
    i: int,
) -> tuple[bool, bool]:
    """ta.crossover / ta.crossunder on bar ``i`` vs prior bar."""
    if i < 1:
        return False, False
    ef = ema_fast_s[i]
    es = ema_slow_s[i]
    ef_prev = ema_fast_s[i - 1]
    es_prev = ema_slow_s[i - 1]
    if ef is None or es is None or ef_prev is None or es_prev is None:
        return False, False
    crossover = ef_prev <= es_prev and ef > es
    crossunder = ef_prev >= es_prev and ef < es
    return crossover, crossunder


def evaluate_pine_bar(
    candles: Sequence[Candle],
    *,
    params: PineParams | None = None,
    now: datetime | None = None,
    prefer_closed: bool = True,
) -> PineBarState | None:
    """Compute Pine features + buy/sell on the last closed bar. None if empty.

    Buy/sell fire only on the bar *after* an EMA cross, when that completed
    candle clears fully above (buy: ``low > max`` prior EMAs) or below
    (sell: ``high < min`` prior EMAs). Touching the lines does not confirm.
    """
    series = (
        prefer_closed_candles(candles, now=now)
        if prefer_closed
        else list(candles)
    )
    if not series:
        return None
    p = params or PineParams()
    closes = [float(c.close) for c in series]
    volumes = [float(c.volume) for c in series]
    n = len(series)
    i = n - 1

    ema_fast_s = _ema_series(closes, int(p.ema_fast))
    ema_slow_s = _ema_series(closes, int(p.ema_slow))
    atr_s, trend_s, _line = supertrend_series(
        series, atr_period=int(p.su_len), multiplier=float(p.su_mult)
    )
    di_p, di_m, adx_s = dmi_adx_series(
        series, di_length=int(p.adx_len), adx_smoothing=int(p.adx_len)
    )
    vol_ma_s = _sma_series(volumes, int(p.vol_sma_len))

    ef = ema_fast_s[i]
    es = ema_slow_s[i]
    crossover, crossunder = _cross_at(ema_fast_s, ema_slow_s, i)
    prior_crossover, prior_crossunder = _cross_at(ema_fast_s, ema_slow_s, i - 1)

    # Confirm full candle clear vs EMAs as of the closed cross bar (i-1).
    candle_clear_above = False
    candle_clear_below = False
    if i >= 1:
        ef_prior = ema_fast_s[i - 1]
        es_prior = ema_slow_s[i - 1]
        if ef_prior is not None and es_prior is not None:
            hi = float(series[i].high)
            lo = float(series[i].low)
            both_hi = max(float(ef_prior), float(es_prior))
            both_lo = min(float(ef_prior), float(es_prior))
            # Strict: wick must not touch / sit on the lines.
            candle_clear_above = lo > both_hi
            candle_clear_below = hi < both_lo

    # Gate: cross on prior bar + next completed candle fully clear. No ST / ADX / vol.
    buy_signal = bool(prior_crossover and candle_clear_above)
    sell_signal = bool(prior_crossunder and candle_clear_below)

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
        prior_crossover=prior_crossover,
        prior_crossunder=prior_crossunder,
        candle_clear_above=candle_clear_above,
        candle_clear_below=candle_clear_below,
        buy_signal=buy_signal,
        sell_signal=sell_signal,
    )


def evaluate_pine_signal(
    symbol: str,
    candles: Sequence[Candle],
    *,
    params: PineParams | None = None,
    cfg: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    prefer_closed: bool = True,
) -> PineSignalResult:
    """Map last-bar Pine buy/sell → BUY / SELL / HOLD with Pine-only reasons."""
    p = params or pine_params_from_config(cfg)
    state = evaluate_pine_bar(
        candles, params=p, now=now, prefer_closed=prefer_closed
    )
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
    if state.prior_crossover:
        reasons.append(
            f"prior bar: EMA{p.ema_fast} crossover EMA{p.ema_slow}"
        )
    elif state.crossover:
        reasons.append(
            f"this bar: EMA{p.ema_fast} crossover EMA{p.ema_slow} (wait next candle clear)"
        )
    if state.prior_crossunder:
        reasons.append(
            f"prior bar: EMA{p.ema_fast} crossunder EMA{p.ema_slow}"
        )
    elif state.crossunder:
        reasons.append(
            f"this bar: EMA{p.ema_fast} crossunder EMA{p.ema_slow} (wait next candle clear)"
        )
    if state.prior_crossover or state.prior_crossunder:
        if state.candle_clear_above:
            reasons.append("candle clear above both EMAs (confirm)")
        elif state.candle_clear_below:
            reasons.append("candle clear below both EMAs (confirm)")
        else:
            reasons.append("candle did not clear both EMAs (touch/sit)")

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
        "prior_crossover": state.prior_crossover,
        "prior_crossunder": state.prior_crossunder,
        "candle_clear_above": state.candle_clear_above,
        "candle_clear_below": state.candle_clear_below,
        "buy_signal": state.buy_signal,
        "sell_signal": state.sell_signal,
        "pine": True,
    }
    conf = 0.85 if (state.buy_signal or state.sell_signal) else 0.0

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
