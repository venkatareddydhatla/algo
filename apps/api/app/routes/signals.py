"""Signal read APIs (mock-backed until persistence wired)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.mock_data import MOCK_SIGNALS, build_universe_signals
from options_portal_domain.config import load_config, watchlist_symbols
from options_portal_hybrid.explain import (
    attach_reason_fields,
    pick_top_hybrid_sides,
    sort_signal_rows,
)
from options_portal_persistence.news_store import NewsStore

router = APIRouter(prefix="/api/v1", tags=["signals"])


def _load_signal_rows(
    *,
    symbol: str | None = None,
    expand_watchlist: bool = True,
) -> tuple[list[dict], str]:
    cfg = load_config()
    trading = cfg.get("trading", {})
    timeframe = str(trading.get("timeframe", "15m"))
    lookback = int(cfg.get("hybrid", {}).get("news_lookback_minutes", 180))

    live_by_symbol: dict = {}
    try:
        store = NewsStore()
        if store.available and store.count() > 0:
            live_by_symbol = store.recent_by_symbol(lookback_minutes=lookback)
    except Exception:  # noqa: BLE001
        live_by_symbol = {}

    if expand_watchlist:
        watchlist = watchlist_symbols(cfg)
        rows = build_universe_signals(
            watchlist,
            timeframe=timeframe,
            live_news_by_symbol=live_by_symbol,
        )
        source = "mock+universe+live-news" if live_by_symbol else "mock+universe"
    else:
        rows = [attach_reason_fields(dict(s)) for s in MOCK_SIGNALS]
        source = "mock"

    if symbol:
        needle = symbol.upper()
        rows = [s for s in rows if s["symbol"].upper() == needle]

    return rows, source


@router.get("/signals")
async def list_signals(
    symbol: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    expand_watchlist: bool = Query(
        True,
        description="Score the full watchlist (dry-run synthetic) instead of seed rows only",
    ),
) -> dict:
    """List signals sorted actionable-first, then by |hybrid| desc.

    Dry-run / mock path returns non-placeholder hybrid scores for every symbol
    in the configured NSE F&O universe (not HOLD/0.00 inactive stubs).
    When live RSS news exists for a symbol, reason/news_score use real headlines.
    """
    rows, source = _load_signal_rows(symbol=symbol, expand_watchlist=expand_watchlist)
    rows = sort_signal_rows(rows)
    total = len(rows)
    page = rows[offset : offset + limit]
    return {
        "items": page,
        "total": total,
        "offset": offset,
        "limit": limit,
        "source": source,
        "sort": "actionable,|hybrid|desc,confidence,symbol",
        "note": (
            "Actionable symbols (non-HOLD) sort first. "
            "Hover reason_summary for reason_detail explainability."
        ),
    }


@router.get("/signals/top-hybrid")
async def top_hybrid(
    limit: int = Query(5, ge=1, le=20),
    expand_watchlist: bool = Query(True),
) -> dict:
    """Top positive (CE) and negative (PE) hybrid strip candidates."""
    rows, source = _load_signal_rows(expand_watchlist=expand_watchlist)
    sides = pick_top_hybrid_sides(rows, limit=limit)
    return {
        "ce": sides["ce"],
        "pe": sides["pe"],
        "limit": limit,
        "source": source,
        "note": (
            "Prefers reason_kind=hybrid; falls back to strongest actionable |hybrid|."
        ),
    }


@router.get("/signals/{signal_id}")
async def get_signal(signal_id: str) -> dict:
    cfg = load_config()
    watchlist = watchlist_symbols(cfg)
    rows = build_universe_signals(
        watchlist,
        timeframe=str(cfg.get("trading", {}).get("timeframe", "15m")),
    )
    for s in rows:
        if s["id"] == signal_id:
            return {
                **s,
                "recommendation": {
                    "rationale": "BUY single-leg ATM (mock)",
                    "legs": [],
                },
                "features": {"rsi": 58.2, "ema_fast": 24490, "ema_slow": 24410},
            }
    raise HTTPException(status_code=404, detail="signal not found")
