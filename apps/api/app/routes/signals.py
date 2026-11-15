"""Signal read APIs (mock-backed until persistence wired)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.mock_data import MOCK_SIGNALS, build_universe_signals
from options_portal_domain.config import load_config, watchlist_symbols
from options_portal_hybrid.explain import attach_reason_fields, sort_signal_rows

router = APIRouter(prefix="/api/v1", tags=["signals"])


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
    """
    cfg = load_config()
    trading = cfg.get("trading", {})
    timeframe = str(trading.get("timeframe", "15m"))

    if expand_watchlist:
        watchlist = watchlist_symbols(cfg)
        rows = build_universe_signals(watchlist, timeframe=timeframe)
        source = "mock+universe"
    else:
        rows = [attach_reason_fields(dict(s)) for s in MOCK_SIGNALS]
        source = "mock"

    if symbol:
        needle = symbol.upper()
        rows = [s for s in rows if s["symbol"].upper() == needle]

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
