"""Thin API wrapper around options_portal_backtest (always dry_run)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from options_portal_backtest.engine import run_backtest
from options_portal_domain.config import load_config, watchlist_symbols

router = APIRouter(prefix="/api/v1", tags=["backtest"])


class BacktestRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=32)
    from_date: date | None = None
    to_date: date | None = None
    bars: int = Field(200, ge=50, le=2000)
    dry_run: bool = True


def _run(
    symbol: str,
    *,
    from_date: date | None,
    to_date: date | None,
    bars: int,
    dry_run: bool,
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
    if watchlist and sym not in watchlist and sym not in {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"}:
        # Soft-allow: still run but note unknown
        note_extra = "symbol not on configured watchlist; synthetic path still allowed"
    else:
        note_extra = ""

    try:
        result = run_backtest(
            sym,
            start=from_date,
            end=to_date,
            bars=bars,
            dry_run=True,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    payload = result.to_dict()
    if note_extra:
        payload["note"] = f"{payload.get('note', '')} {note_extra}".strip()
    return payload


@router.post("/backtest/run")
async def backtest_run(body: BacktestRequest) -> dict:
    return _run(
        body.symbol,
        from_date=body.from_date,
        to_date=body.to_date,
        bars=body.bars,
        dry_run=body.dry_run,
    )


@router.get("/backtest/run")
async def backtest_run_get(
    symbol: str = Query(..., min_length=1),
    from_date: date | None = None,
    to_date: date | None = None,
    bars: int = Query(200, ge=50, le=2000),
    dry_run: bool = Query(True),
) -> dict:
    return _run(
        symbol,
        from_date=from_date,
        to_date=to_date,
        bars=bars,
        dry_run=dry_run,
    )
