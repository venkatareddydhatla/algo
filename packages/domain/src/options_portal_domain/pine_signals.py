"""NPR21 Reversal Detection Pro v3.2 — confirmed-only equity signal engine.

Faithful Python port of the TradingView Pine logic (non-repainting Confirmed
Only path). Chart labels / zones / info table are omitted — signal logic only.

On closed 15m bars (prefer_closed):

- ZigZag pivots via ATR-adaptive reversal threshold
- Signal state machine → **U1** (bullish flip) / **D1** (bearish flip)
- Triple EMA (9/14/21) → ``buysignal`` / ``sellsignal`` trend state
- **STRONG** = U1 + buysignal==1 / D1 + sellsignal==1

Defaults match Pine inputs: Confirmed Only, Medium sensitivity, ATR length 14,
method ``average``, avg length 5, confirmationBars 0.

Trade mapping (dry-run):
  U1 → BUY / LONG; D1 → SELL / SHORT.
  Default ``require_strong=True`` (reversal + EMA trend aligned). Set
  ``trading.pine_signals.require_strong: false`` to enter on raw U1/D1.

Preview pivots are never used for trading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from options_portal_domain.types import Candle, SignalAction

DEFAULT_CONFIRMATION_MODE = "Confirmed Only"
DEFAULT_CONFIRMATION_BARS = 0
DEFAULT_SENSITIVITY = "Medium"
DEFAULT_METHOD = "average"
DEFAULT_PERCENT_AMOUNT = 0.08
DEFAULT_REV_AMOUNT = 0.0001
DEFAULT_ATR_REVERSAL = 2.0
DEFAULT_ATR_LENGTH = 14
DEFAULT_AVERAGE_LENGTH = 5
DEFAULT_REQUIRE_STRONG = True
DEFAULT_EMA_SUPERFAST = 9
DEFAULT_EMA_FAST = 14
DEFAULT_EMA_SLOW = 21
DEFAULT_TIMEFRAME_MINUTES = 15

SENSITIVITY_ATR_MULT: dict[str, float] = {
    "Very High": 0.8,
    "High": 1.2,
    "Medium": 2.0,
    "Low": 2.8,
    "Very Low": 3.5,
}

SENSITIVITY_PCT: dict[str, float] = {
    "Very High": 0.02,
    "High": 0.03,
    "Medium": 0.05,
    "Low": 0.06,
    "Very Low": 0.08,
}

SIGNAL_ENGINE_ID = "reversal_pro_v32"


@dataclass(frozen=True)
class PineParams:
    """Reversal Pro v3.2 inputs (Confirmed Only trading path)."""

    confirmation_mode: str = DEFAULT_CONFIRMATION_MODE
    confirmation_bars: int = DEFAULT_CONFIRMATION_BARS
    sensitivity: str = DEFAULT_SENSITIVITY
    method: str = DEFAULT_METHOD
    percent_amount: float = DEFAULT_PERCENT_AMOUNT
    rev_amount: float = DEFAULT_REV_AMOUNT
    atr_reversal: float = DEFAULT_ATR_REVERSAL
    atr_length: int = DEFAULT_ATR_LENGTH
    average_length: int = DEFAULT_AVERAGE_LENGTH
    require_strong: bool = DEFAULT_REQUIRE_STRONG
    ema_superfast: int = DEFAULT_EMA_SUPERFAST
    ema_fast: int = DEFAULT_EMA_FAST
    ema_slow: int = DEFAULT_EMA_SLOW

    @property
    def is_custom(self) -> bool:
        return str(self.sensitivity).strip().lower() == "custom"

    def atr_multiplier(self) -> float:
        if self.is_custom:
            return float(self.atr_reversal)
        return float(SENSITIVITY_ATR_MULT.get(self.sensitivity, SENSITIVITY_ATR_MULT["Medium"]))

    def percent_threshold(self) -> float:
        if self.is_custom:
            return float(self.percent_amount)
        return float(SENSITIVITY_PCT.get(self.sensitivity, SENSITIVITY_PCT["Medium"]))


@dataclass(frozen=True)
class PineBarState:
    """Per-bar Reversal Pro snapshot (last bar is the decision bar)."""

    ema9: float | None
    ema14: float | None
    ema21: float | None
    atr: float | None
    reversal_amount: float | None
    direction: int  # zigzag direction +1 / -1
    signal: int  # -1 / 0 / +1
    u1: bool
    d1: bool
    buysignal: int  # 0 or 1
    sellsignal: int  # 0 or 1
    strong_bullish: bool
    strong_bearish: bool
    confirmed_pivot: bool
    buy_signal: bool  # entry gate (U1 or STRONG)
    sell_signal: bool
    # Compat aliases used by older call sites / logs
    ema_fast: float | None = None
    ema_slow: float | None = None
    crossover: bool = False
    crossunder: bool = False
    prior_crossover: bool = False
    prior_crossunder: bool = False
    candle_clear_above: bool = False
    candle_clear_below: bool = False


@dataclass(frozen=True)
class PineSignalResult:
    """Equity dry-run action derived from Reversal Pro U1/D1 (or STRONG)."""

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
            if self.state and self.state.strong_bullish:
                return "STRONG bullish (U1 + EMA trend)"
            return "U1 bullish reversal"
        if self.sell_signal:
            if self.state and self.state.strong_bearish:
                return "STRONG bearish (D1 + EMA trend)"
            return "D1 bearish reversal"
        return "no Reversal Pro signal"

    @property
    def reason_detail(self) -> str:
        if not self.reasons:
            return "Neutral: no U1/D1 on last bar"
        body = "\n".join(f"- {r}" for r in self.reasons)
        return f"Technical:\n{body}"


def pine_params_from_config(cfg: Mapping[str, Any] | None) -> PineParams:
    """Read ``trading.pine_signals`` (or ``ta.pine``) with Reversal Pro defaults."""
    root = dict(cfg or {})
    trading = dict(root.get("trading") or {})
    raw = dict(trading.get("pine_signals") or root.get("pine_signals") or {})
    if not raw:
        ta = dict(root.get("ta") or {})
        raw = dict(ta.get("pine") or {})
    return PineParams(
        confirmation_mode=str(
            raw.get("confirmation_mode", DEFAULT_CONFIRMATION_MODE)
        ),
        confirmation_bars=int(raw.get("confirmation_bars", DEFAULT_CONFIRMATION_BARS)),
        sensitivity=str(raw.get("sensitivity", DEFAULT_SENSITIVITY)),
        method=str(raw.get("method", DEFAULT_METHOD)),
        percent_amount=float(raw.get("percent_amount", DEFAULT_PERCENT_AMOUNT)),
        rev_amount=float(raw.get("rev_amount", DEFAULT_REV_AMOUNT)),
        atr_reversal=float(raw.get("atr_reversal", DEFAULT_ATR_REVERSAL)),
        atr_length=int(raw.get("atr_length", DEFAULT_ATR_LENGTH)),
        average_length=int(raw.get("average_length", DEFAULT_AVERAGE_LENGTH)),
        require_strong=bool(raw.get("require_strong", DEFAULT_REQUIRE_STRONG)),
        ema_superfast=int(raw.get("ema_superfast", DEFAULT_EMA_SUPERFAST)),
        ema_fast=int(raw.get("ema_fast", DEFAULT_EMA_FAST)),
        ema_slow=int(raw.get("ema_slow", DEFAULT_EMA_SLOW)),
    )


def pine_signals_enabled(cfg: Mapping[str, Any] | None) -> bool:
    """Pine / Reversal Pro engine is the primary equity dry-run path when enabled."""
    root = dict(cfg or {})
    trading = dict(root.get("trading") or {})
    raw = dict(trading.get("pine_signals") or root.get("pine_signals") or {})
    return bool(raw.get("enabled", True))


def _ensure_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def parse_pine_bar_ts(value: datetime | str | None) -> datetime | None:
    """Parse a persisted ``bar_ts`` (datetime or ISO string) to UTC."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return _ensure_utc(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _ensure_utc(parsed)


def floor_to_timeframe(
    ts: datetime,
    *,
    timeframe_minutes: int = DEFAULT_TIMEFRAME_MINUTES,
) -> datetime:
    """Floor ``ts`` to the open of its ``timeframe_minutes`` bucket (UTC)."""
    clock = _ensure_utc(ts)
    tf = max(1, int(timeframe_minutes))
    minute = (clock.minute // tf) * tf
    return clock.replace(minute=minute, second=0, microsecond=0)


def latest_closed_bar_open(
    now: datetime | None = None,
    *,
    timeframe_minutes: int = DEFAULT_TIMEFRAME_MINUTES,
) -> datetime:
    """Open time of the latest completed bar under ``prefer_closed_candles`` rules."""
    clock = _ensure_utc(now or datetime.now(tz=timezone.utc))
    tf = max(1, int(timeframe_minutes))
    bucket = floor_to_timeframe(clock, timeframe_minutes=tf)
    return bucket - timedelta(minutes=tf)


def pine_signal_bar_is_current(
    bar_ts: datetime | str | None,
    *,
    now: datetime | None = None,
    timeframe_minutes: int = DEFAULT_TIMEFRAME_MINUTES,
) -> bool:
    """True while the signal bar is still current for watchlist Action."""
    ts = parse_pine_bar_ts(bar_ts)
    if ts is None:
        return False
    clock = _ensure_utc(now or datetime.now(tz=timezone.utc))
    tf = max(1, int(timeframe_minutes))
    signal_bucket = floor_to_timeframe(ts, timeframe_minutes=tf)
    now_bucket = floor_to_timeframe(clock, timeframe_minutes=tf)
    if signal_bucket == now_bucket:
        return True
    latest_closed = latest_closed_bar_open(clock, timeframe_minutes=tf)
    return signal_bucket == latest_closed


def watchlist_pine_action(
    action: str | SignalAction | None,
    bar_ts: datetime | str | None,
    *,
    now: datetime | None = None,
    timeframe_minutes: int = DEFAULT_TIMEFRAME_MINUTES,
) -> str:
    """Map a Pine action to watchlist BUY/SELL/HOLD with signal-bar expiry."""
    raw = action.value if isinstance(action, SignalAction) else str(action or "HOLD")
    act = raw.upper()
    if act in {"BUY_CE_BIAS", "BUY_CE", "LONG"}:
        act = "BUY"
    elif act in {"BUY_PE_BIAS", "BUY_PE", "SHORT"}:
        act = "SELL"
    elif act not in {"BUY", "SELL", "HOLD"}:
        act = "HOLD"
    if act == "HOLD":
        return "HOLD"
    if pine_signal_bar_is_current(
        bar_ts, now=now, timeframe_minutes=timeframe_minutes
    ):
        return act
    return "HOLD"


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


def _shift(series: Sequence[float | None], bars: int, i: int) -> float | None:
    """Pine ``series[bars]`` at bar index ``i`` (bars>=0 lookback)."""
    j = i - int(bars)
    if j < 0 or j >= len(series):
        return None
    return series[j]


def _run_reversal_engine(
    series: Sequence[Candle],
    *,
    params: PineParams,
) -> list[PineBarState]:
    """Bar-by-bar ZigZag + signal + EMA trend state (Confirmed Only path)."""
    n = len(series)
    if n == 0:
        return []

    p = params
    conf_bars = max(0, int(p.confirmation_bars))
    # Trading path is Confirmed Only — preview ZigZag is never evaluated.
    closes = [float(c.close) for c in series]
    highs = [float(c.high) for c in series]
    lows = [float(c.low) for c in series]

    method = str(p.method).strip().lower()
    if method == "high_low":
        priceh: list[float | None] = list(highs)  # type: ignore[assignment]
        pricel: list[float | None] = list(lows)  # type: ignore[assignment]
    else:
        avg_len = max(1, int(p.average_length))
        priceh = _ema_series(highs, avg_len)
        pricel = _ema_series(lows, avg_len)

    atr_s = atr_rma_series(series, max(1, int(p.atr_length)))
    ema9_s = _ema_series(closes, max(1, int(p.ema_superfast)))
    ema14_s = _ema_series(closes, max(1, int(p.ema_fast)))
    ema21_s = _ema_series(closes, max(1, int(p.ema_slow)))

    atr_mult = p.atr_multiplier()
    pct_thr = p.percent_threshold()
    rev_abs = float(p.rev_amount)

    zhigh: float | None = None
    zlow: float | None = None
    zhigh_actual: float | None = None
    zlow_actual: float | None = None
    zhighbar = 0
    zlowbar = 0
    direction = 0

    eil: float | None = None
    eih: float | None = None
    direction_sig = 0  # Pine ``dir``
    signal = 0
    buysignal = 0
    sellsignal = 0
    buy_prev = False
    sell_prev = False

    states: list[PineBarState] = []

    for i in range(n):
        atr_v = atr_s[i]
        close_v = closes[i]
        if atr_v is None:
            rev_amt: float | None = None
        else:
            rev_amt = max(
                close_v * pct_thr / 100.0,
                max(rev_abs, atr_mult * float(atr_v)),
            )

        ph_c = _shift(priceh, conf_bars, i)
        pl_c = _shift(pricel, conf_bars, i)
        ah_c = _shift(highs, conf_bars, i)  # type: ignore[arg-type]
        al_c = _shift(lows, conf_bars, i)  # type: ignore[arg-type]

        confirmed_pivot = False
        last_pivot_is_high = False
        last_pivot_price: float | None = None
        last_pivot_actual: float | None = None
        last_pivot_bar = 0

        if (
            ph_c is not None
            and pl_c is not None
            and ah_c is not None
            and al_c is not None
            and rev_amt is not None
        ):
            if zhigh is None or zlow is None:
                zhigh = float(ph_c)
                zlow = float(pl_c)
                zhigh_actual = float(ah_c)
                zlow_actual = float(al_c)
                zhighbar = i - conf_bars
                zlowbar = i - conf_bars
                direction = 1
            elif direction == 1:
                if float(ph_c) > float(zhigh):
                    zhigh = float(ph_c)
                    zhigh_actual = float(ah_c)
                    zhighbar = i - conf_bars
                if float(zhigh) - float(pl_c) >= float(rev_amt):
                    last_pivot_price = float(zhigh)
                    last_pivot_actual = float(zhigh_actual) if zhigh_actual is not None else float(zhigh)
                    last_pivot_bar = zhighbar
                    last_pivot_is_high = True
                    confirmed_pivot = True
                    direction = -1
                    zlow = float(pl_c)
                    zlow_actual = float(al_c)
                    zlowbar = i - conf_bars
            elif direction == -1:
                if float(pl_c) < float(zlow):
                    zlow = float(pl_c)
                    zlow_actual = float(al_c)
                    zlowbar = i - conf_bars
                if float(ph_c) - float(zlow) >= float(rev_amt):
                    last_pivot_price = float(zlow)
                    last_pivot_actual = float(zlow_actual) if zlow_actual is not None else float(zlow)
                    last_pivot_bar = zlowbar
                    last_pivot_is_high = False
                    confirmed_pivot = True
                    direction = 1
                    zhigh = float(ph_c)
                    zhigh_actual = float(ah_c)
                    zhighbar = i - conf_bars

        if confirmed_pivot and last_pivot_price is not None:
            if last_pivot_is_high:
                eih = last_pivot_price
                direction_sig = -1
            else:
                eil = last_pivot_price
                direction_sig = 1
            # last_pivot_actual / last_pivot_bar unused for trading (labels only)
            _ = (last_pivot_actual, last_pivot_bar)

        prev_signal = signal
        if (
            ph_c is not None
            and pl_c is not None
            and direction_sig > 0
            and eil is not None
            and float(pl_c) > float(eil)
        ):
            if signal <= 0:
                signal = 1
        elif (
            ph_c is not None
            and pl_c is not None
            and direction_sig < 0
            and eih is not None
            and float(ph_c) < float(eih)
        ):
            if signal >= 0:
                signal = -1

        u1 = signal > 0 and prev_signal <= 0
        d1 = signal < 0 and prev_signal >= 0

        # Triple EMA trend state machine (Pine buysignal / sellsignal)
        e9 = ema9_s[i]
        e14 = ema14_s[i]
        e21 = ema21_s[i]
        hi = highs[i]
        lo = lows[i]
        buy = False
        stopbuy = False
        sell = False
        stopsell = False
        if e9 is not None and e14 is not None and e21 is not None:
            buy = float(e9) > float(e14) and float(e14) > float(e21) and lo > float(e9)
            stopbuy = float(e9) <= float(e14)
            sell = float(e9) < float(e14) and float(e14) < float(e21) and hi < float(e9)
            stopsell = float(e9) >= float(e14)

        buynow = (not buy_prev) and buy
        sellnow = (not sell_prev) and sell
        if buynow and not stopbuy:
            buysignal = 1
        elif buysignal == 1 and stopbuy:
            buysignal = 0
        if sellnow and not stopsell:
            sellsignal = 1
        elif sellsignal == 1 and stopsell:
            sellsignal = 0
        buy_prev = buy
        sell_prev = sell

        strong_bull = bool(u1 and buysignal == 1)
        strong_bear = bool(d1 and sellsignal == 1)

        if p.require_strong:
            entry_buy = strong_bull
            entry_sell = strong_bear
        else:
            entry_buy = bool(u1)
            entry_sell = bool(d1)

        states.append(
            PineBarState(
                ema9=e9,
                ema14=e14,
                ema21=e21,
                atr=atr_v,
                reversal_amount=rev_amt,
                direction=int(direction),
                signal=int(signal),
                u1=bool(u1),
                d1=bool(d1),
                buysignal=int(buysignal),
                sellsignal=int(sellsignal),
                strong_bullish=strong_bull,
                strong_bearish=strong_bear,
                confirmed_pivot=bool(confirmed_pivot),
                buy_signal=entry_buy,
                sell_signal=entry_sell,
                ema_fast=e14,
                ema_slow=e21,
            )
        )

    return states


def evaluate_pine_bar(
    candles: Sequence[Candle],
    *,
    params: PineParams | None = None,
    now: datetime | None = None,
    prefer_closed: bool = True,
) -> PineBarState | None:
    """Compute Reversal Pro features + entry gates on the last closed bar."""
    series = (
        prefer_closed_candles(candles, now=now)
        if prefer_closed
        else list(candles)
    )
    if not series:
        return None
    p = params or PineParams()
    states = _run_reversal_engine(series, params=p)
    return states[-1] if states else None


def evaluate_pine_signal(
    symbol: str,
    candles: Sequence[Candle],
    *,
    params: PineParams | None = None,
    cfg: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    prefer_closed: bool = True,
) -> PineSignalResult:
    """Map last-bar U1/D1 (or STRONG) → BUY / SELL / HOLD."""
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
    reasons.append(
        f"mode={p.confirmation_mode} sensitivity={p.sensitivity} "
        f"require_strong={p.require_strong}"
    )
    if state.ema9 is not None and state.ema14 is not None and state.ema21 is not None:
        reasons.append(
            f"EMA9={state.ema9:.4f} EMA14={state.ema14:.4f} EMA21={state.ema21:.4f}"
        )
    if state.atr is not None and state.reversal_amount is not None:
        reasons.append(
            f"ATR={state.atr:.4f} revAmt={state.reversal_amount:.4f} "
            f"(atrMult={p.atr_multiplier():.2f} pct={p.percent_threshold():.2f}%)"
        )
    reasons.append(
        f"signal={state.signal} u1={state.u1} d1={state.d1} "
        f"buysignal={state.buysignal} sellsignal={state.sellsignal}"
    )
    if state.u1:
        reasons.append("U1 bullish reversal flip")
    if state.d1:
        reasons.append("D1 bearish reversal flip")
    if state.strong_bullish:
        reasons.append("STRONG bullish (U1 + buysignal)")
    if state.strong_bearish:
        reasons.append("STRONG bearish (D1 + sellsignal)")

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
        "ema9": state.ema9,
        "ema14": state.ema14,
        "ema21": state.ema21,
        "ema_fast": state.ema14,
        "ema_slow": state.ema21,
        "atr": state.atr,
        "reversal_amount": state.reversal_amount,
        "atr_multiplier": p.atr_multiplier(),
        "percent_threshold": p.percent_threshold(),
        "sensitivity": p.sensitivity,
        "confirmation_mode": p.confirmation_mode,
        "confirmation_bars": p.confirmation_bars,
        "method": p.method,
        "require_strong": p.require_strong,
        "zigzag_direction": state.direction,
        "signal": state.signal,
        "u1": state.u1,
        "d1": state.d1,
        "buysignal": state.buysignal,
        "sellsignal": state.sellsignal,
        "strong_bullish": state.strong_bullish,
        "strong_bearish": state.strong_bearish,
        "confirmed_pivot": state.confirmed_pivot,
        "buy_signal": state.buy_signal,
        "sell_signal": state.sell_signal,
        "pine": True,
        "signal_engine": SIGNAL_ENGINE_ID,
    }
    conf = 0.9 if (state.strong_bullish or state.strong_bearish) else (
        0.85 if (state.buy_signal or state.sell_signal) else 0.0
    )

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
