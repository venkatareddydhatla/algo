"""News feed read API — live SQLite rows when ingest has run; else gated mock.

Portal News shows **today's live feed only** (IST). Archived history is not exposed
here (optional admin/internal API later). See docs/options-portal-news-retention.md.
"""

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
    # Retention: portal UI = today's IST live feed only (history never returned).
    store = NewsStore()
    live: list = []
    if store.available:
        # Opportunistic retention so Windows-native API-only mode still archives.
        store.run_retention()
        live = store.list_recent(
            symbol=symbol,
            live_today_only=True,
            limit=limit,
        )

    if live:
        return {
            "items": live,
            "source": "live",
            "count": len(live),
            "scope": "today_ist",
            "note": "Live feed for today (IST). Prior days are archived off this page.",
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
        "scope": "today_ist",
        "note": (
            "No live headlines yet for today (IST). Start the worker (or wait for the "
            "API news poller) so RSS feeds can populate SQLite. Older items are archived."
        ),
    }
