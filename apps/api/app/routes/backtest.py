"""Thin API wrapper around options_portal_backtest (always dry_run).

Prefers Upstox historical candles. Default refuse-synthetic so win-rate/PnL
cannot be mistaken for tradeable expectancy.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from options_portal_backtest.candles import RealHistoryUnavailable
from options_portal_backtest.engine import run_backtest
from options_portal_domain.config import load_config, watchlist_symbols
from options_portal_domain.tz import ist

router = APIRouter(prefix="/api/v1", tags=["backtest"])

IST = ist()
# ~25 fifteen-minute bars per NSE cash session
_BARS_PER_SESSION = 25


class BacktestRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=32)
    from_date: date | None = None
    to_date: date | None = None
    bars: int = Field(200, ge=50, le=2000)
    dry_run: bool = True
    allow_synthetic: bool = False


def _window_datetimes(
    *,
    from_date: date | None,
    to_date: date | None,
    bars: int,
) -> tuple[datetime, datetime]:
    """IST session window covering the requested bars / calendar range."""
    end_d = to_date or date.today()
    if from_date is not None:
        start_d = from_date
    else:
        days_needed = max(10, int((bars / _BARS_PER_SESSION) * 2) + 5)
        start_d = end_d - timedelta(days=days_needed)
    start_dt = datetime.combine(start_d, time(9, 15), tzinfo=IST)
    end_dt = datetime.combine(end_d, time(15, 30), tzinfo=IST)
    return start_dt, end_dt


async def _fetch_upstox_candles(symbol: str, start: datetime, end: datetime):
    from options_portal_broker.adapter import UpstoxAdapter

    broker = UpstoxAdapter()
    candles = await broker.get_real_candles(symbol, "15m", start, end)
    detail = broker.last_candle_detail
    source = broker.last_candle_source
    if candles and source == "upstox":
        return list(candles), "upstox", detail
    return [], source or "unavailable", detail


async def _run(
    symbol: str,
    *,
    from_date: date | None,
    to_date: date | None,
    bars: int,
    dry_run: bool,
    allow_synthetic: bool,
) -> dict:
    if not dry_run:
        raise HTTPException(
            status_code=400,
            detail="Only dry_run backtests are allowed in v1",
        )
    sym = symbol.upper().strip()
    cfg = load_config()
    watchlist = {s.upper() for s in watchlist_symbols(cfg)}
    # Allow common indices even if watchlist file is truncated in tests
    if watchlist and sym not in watchlist and sym not in {
        "NIFTY",
        "BANKNIFTY",
        "FINNIFTY",
        "MIDCPNIFTY",
        "SENSEX",
    }:
        note_extra = "symbol not on configured watchlist"
    else:
        note_extra = ""

    start_dt, end_dt = _window_datetimes(
        from_date=from_date, to_date=to_date, bars=bars
    )
    preloaded, candle_source, detail = await _fetch_upstox_candles(
        sym, start_dt, end_dt
    )

    try:
        result = run_backtest(
            sym,
            start=from_date,
            end=to_date,
            bars=bars,
            dry_run=True,
            allow_synthetic=allow_synthetic,
            candles=preloaded or None,
            candle_source=candle_source if preloaded else None,
        )
    except RealHistoryUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    payload = result.to_dict()
    extras: list[str] = []
    if note_extra:
        extras.append(note_extra)
    if not preloaded and detail:
        extras.append(f"upstox: {detail}")
    if extras:
        payload["note"] = f"{payload.get('note', '')} {' '.join(extras)}".strip()
    return payload


@router.post("/backtest/run")
async def backtest_run(body: BacktestRequest) -> dict:
    return await _run(
        body.symbol,
        from_date=body.from_date,
        to_date=body.to_date,
        bars=body.bars,
        dry_run=body.dry_run,
        allow_synthetic=body.allow_synthetic,
    )


@router.get("/backtest/run")
async def backtest_run_get(
    symbol: str = Query(..., min_length=1),
    from_date: date | None = None,
    to_date: date | None = None,
    bars: int = Query(200, ge=50, le=2000),
    dry_run: bool = Query(True),
    allow_synthetic: bool = Query(False),
) -> dict:
    return await _run(
        symbol,
        from_date=from_date,
        to_date=to_date,
        bars=bars,
        dry_run=dry_run,
        allow_synthetic=allow_synthetic,
    )
