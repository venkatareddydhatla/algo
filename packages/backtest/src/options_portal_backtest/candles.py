"""Load real or synthesize 15m candles for dry-run backtests.

Default path refuses silent synthetic bars so UI win-rate / PnL cannot be
mistaken for tradeable expectancy. Synthetic is opt-in via ``allow_synthetic``.
"""

from __future__ import annotations

import csv
import hashlib
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from options_portal_domain.tz import ist
from options_portal_domain.types import Candle, Timeframe

IST = ist()


class RealHistoryUnavailable(RuntimeError):
    """Raised when fixture/Upstox history is missing and synthetic is refused."""


def fixture_dir() -> Path:
    # packages/backtest/src/options_portal_backtest/candles.py → repo root
    return Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "candles"


def load_candles_csv(path: Path, *, symbol: str, timeframe: Timeframe = "15m") -> list[Candle]:
    rows: list[Candle] = []
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            sym = (raw.get("symbol") or symbol).upper()
            if sym != symbol.upper():
                continue
            ts = datetime.fromisoformat(raw["ts"].replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            rows.append(
                Candle(
                    symbol=sym,
                    timeframe=timeframe,
                    ts=ts.astimezone(timezone.utc),
                    open=float(raw["open"]),
                    high=float(raw["high"]),
                    low=float(raw["low"]),
                    close=float(raw["close"]),
                    volume=int(float(raw.get("volume") or 0)),
                    oi=None,
                )
            )
    rows.sort(key=lambda c: c.ts)
    return rows


def _seed_float(symbol: str) -> float:
    digest = hashlib.sha256(symbol.upper().encode()).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


def synthesize_candles(
    symbol: str,
    *,
    start: date,
    end: date,
    bars: int | None = None,
    timeframe: Timeframe = "15m",
    base_price: float | None = None,
) -> list[Candle]:
    """Deterministic synthetic 15m session bars (IST weekdays only)."""
    seed = _seed_float(symbol)
    px = base_price if base_price is not None else 100.0 + seed * 2400.0
    # ~25 bars/session for 15m NSE cash (09:15–15:30)
    bars_per_day = 25
    out: list[Candle] = []
    day = start
    while day <= end:
        if day.weekday() < 5:
            session_open = datetime(day.year, day.month, day.day, 9, 15, tzinfo=IST)
            for i in range(bars_per_day):
                t = session_open + timedelta(minutes=15 * i)
                # Mild drift + sinusoidal noise keyed by seed
                phase = (len(out) + 1) * (0.17 + seed * 0.11)
                shock = math.sin(phase) * (0.004 + seed * 0.003) + math.cos(
                    phase * 0.37
                ) * 0.002
                drift = (seed - 0.5) * 0.0004
                open_px = px
                close_px = max(1.0, px * (1.0 + drift + shock))
                high_px = max(open_px, close_px) * (1.0 + abs(shock) * 0.35)
                low_px = min(open_px, close_px) * (1.0 - abs(shock) * 0.35)
                vol = int(50_000 + seed * 200_000 + (i % 7) * 1_200)
                out.append(
                    Candle(
                        symbol=symbol.upper(),
                        timeframe=timeframe,
                        ts=t.astimezone(timezone.utc),
                        open=round(open_px, 4),
                        high=round(high_px, 4),
                        low=round(low_px, 4),
                        close=round(close_px, 4),
                        volume=vol,
                    )
                )
                px = close_px
                if bars is not None and len(out) >= bars:
                    return out
        day += timedelta(days=1)
    if bars is not None and len(out) > bars:
        return out[-bars:]
    return out


def _refuse_message(symbol: str, detail: str | None = None) -> str:
    base = (
        f"No real history for {symbol.upper()}; "
        "check Upstox token / instrument / dates"
    )
    if detail:
        return f"{base} ({detail})"
    return base


def resolve_candles(
    symbol: str,
    *,
    start: date | None = None,
    end: date | None = None,
    bars: int | None = None,
    fixture_path: Path | None = None,
    allow_synthetic: bool = False,
    preloaded: list[Candle] | None = None,
    preloaded_source: str | None = None,
    min_bars: int = 40,
) -> tuple[list[Candle], str]:
    """Resolve candles for backtest.

    Preference order:
    1. Explicit ``preloaded`` bars (e.g. Upstox historical from API)
    2. Fixture CSV when present
    3. Synthetic only when ``allow_synthetic=True``
    4. Otherwise raise :class:`RealHistoryUnavailable`
    """
    sym = symbol.upper().strip()

    if preloaded is not None:
        candles = list(preloaded)
        if start:
            candles = [c for c in candles if c.ts.astimezone(IST).date() >= start]
        if end:
            candles = [c for c in candles if c.ts.astimezone(IST).date() <= end]
        if bars is not None and len(candles) > bars:
            candles = candles[-bars:]
        source = preloaded_source or "upstox"
        if source.startswith("synthetic") and not allow_synthetic:
            raise RealHistoryUnavailable(_refuse_message(sym, "synthetic refused"))
        if len(candles) >= min_bars and not source.startswith("synthetic"):
            return candles, source
        if len(candles) >= min_bars and source.startswith("synthetic") and allow_synthetic:
            return candles, source
        if not allow_synthetic:
            raise RealHistoryUnavailable(
                _refuse_message(
                    sym,
                    f"incomplete history: {len(candles)} bars < {min_bars}",
                )
            )
        # Incomplete real history + allow_synthetic → fall through to synthesize

    path = fixture_path
    if path is None:
        candidate = fixture_dir() / f"{sym}.csv"
        if candidate.is_file():
            path = candidate
    if path is not None and path.is_file():
        candles = load_candles_csv(path, symbol=sym)
        if start:
            candles = [c for c in candles if c.ts.astimezone(IST).date() >= start]
        if end:
            candles = [c for c in candles if c.ts.astimezone(IST).date() <= end]
        if bars is not None and len(candles) > bars:
            candles = candles[-bars:]
        if len(candles) >= min_bars:
            return candles, f"fixture:{path.name}"
        if not allow_synthetic:
            raise RealHistoryUnavailable(
                _refuse_message(
                    sym,
                    f"fixture incomplete: {len(candles)} bars < {min_bars}",
                )
            )
        # Incomplete fixture + allow_synthetic → fall through to synthesize

    if not allow_synthetic:
        raise RealHistoryUnavailable(_refuse_message(sym))

    today = date.today()
    end_d = end or today
    if bars is not None and bars > 0:
        # ~25 bars/day → enough calendar days with weekend slack
        days_needed = max(10, int(math.ceil(bars / 25) * 2) + 5)
        start_d = start or (end_d - timedelta(days=days_needed))
    else:
        start_d = start or (end_d - timedelta(days=60))
    candles = synthesize_candles(sym, start=start_d, end=end_d, bars=bars)
    return candles, "synthetic"
