"""News feed read API."""

from __future__ import annotations

from fastapi import APIRouter

from app.mock_data import MOCK_NEWS

router = APIRouter(prefix="/api/v1", tags=["news"])


@router.get("/news")
async def list_news(symbol: str | None = None) -> dict:
    rows = MOCK_NEWS
    if symbol:
        sym = symbol.upper()
        rows = [n for n in rows if sym in n.get("symbols", [])]
    return {"items": rows, "source": "mock"}
