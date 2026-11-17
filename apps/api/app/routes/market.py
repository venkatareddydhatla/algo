"""Market status + index strip quotes (NIFTY / SENSEX)."""

from __future__ import annotations

from fastapi import APIRouter

from app.index_quotes import build_index_strip_payload

router = APIRouter(tags=["market"])


@router.get("/api/v1/market/indices")
async def market_indices() -> dict:
    """Watchlist Signals strip: OPEN/CLOSE + NIFTY/SENSEX last (change when cheap).

    Quotes only — does not place orders. When market is CLOSE, returns last-known
    cached values (stale) or null prices; never invents marks.
    """
    return await build_index_strip_payload()
