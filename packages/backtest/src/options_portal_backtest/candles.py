"""Load or synthesize 15m candles for dry-run backtests."""

from __future__ import annotations

import csv
import hashlib
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from options_portal_domain.types import Candle, Timeframe

IST = ZoneInfo("Asia/Kolkata")


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


def resolve_candles(
    symbol: str,
    *,
    start: date | None = None,
    end: date | None = None,
    bars: int | None = None,
    fixture_path: Path | None = None,
) -> tuple[list[Candle], str]:
    """Load fixture CSV when present; otherwise synthesize dry-run bars."""
    path = fixture_path
    if path is None:
        candidate = fixture_dir() / f"{symbol.upper()}.csv"
        if candidate.is_file():
            path = candidate
    if path is not None and path.is_file():
        candles = load_candles_csv(path, symbol=symbol)
        if start:
            candles = [c for c in candles if c.ts.date() >= start]
        if end:
            candles = [c for c in candles if c.ts.date() <= end]
        if bars is not None and len(candles) > bars:
            candles = candles[-bars:]
        return candles, f"fixture:{path.name}"

    today = date.today()
    end_d = end or today
    if bars is not None and bars > 0:
        # ~25 bars/day → enough calendar days with weekend slack
        days_needed = max(10, int(math.ceil(bars / 25) * 2) + 5)
        start_d = start or (end_d - timedelta(days=days_needed))
    else:
        start_d = start or (end_d - timedelta(days=60))
    candles = synthesize_candles(
        symbol, start=start_d, end=end_d, bars=bars
    )
    return candles, "synthetic"
