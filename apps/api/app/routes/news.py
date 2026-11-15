"""News feed read API — live SQLite rows when ingest has run; else gated mock."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.mock_data import MOCK_NEWS
from options_portal_domain.config import load_config
from options_portal_persistence.news_store import NewsStore

router = APIRouter(prefix="/api/v1", tags=["news"])


@router.get("/news")
async def list_news(
    symbol: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    allow_mock: bool = Query(
        False,
        description="If true and no live rows, return seed mock items (default: empty live)",
    ),
) -> dict:
    cfg = load_config()
    lookback = int(cfg.get("hybrid", {}).get("news_lookback_minutes", 180))
    # Show a wider window on the News page than the hybrid lookback
    page_lookback = max(lookback, int(cfg.get("news", {}).get("ui_lookback_minutes", 720)))

    store = NewsStore()
    live: list = []
    if store.available:
        live = store.list_recent(
            symbol=symbol,
            lookback_minutes=page_lookback,
            limit=limit,
        )

    if live:
        return {
            "items": live,
            "source": "live",
            "count": len(live),
            "lookback_minutes": page_lookback,
        }

    if allow_mock:
        rows = MOCK_NEWS
        if symbol:
            sym = symbol.upper()
            rows = [n for n in rows if sym in n.get("symbols", [])]
        return {"items": rows, "source": "mock", "count": len(rows)}

    return {
        "items": [],
        "source": "live",
        "count": 0,
        "note": (
            "No live headlines yet. Start the worker (or wait for the API news poller) "
            "so RSS feeds can populate SQLite."
        ),
    }
