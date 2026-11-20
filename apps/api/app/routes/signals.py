"""Signal read APIs (mock-backed until persistence wired)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.marks import enrich_rows_with_prices
from app.mock_data import MOCK_SIGNALS, build_universe_signals
from options_portal_domain.config import load_config, watchlist_symbols
from options_portal_hybrid.explain import (
    pick_top_hybrid_sides,
    sort_signal_rows,
)

router = APIRouter(prefix="/api/v1", tags=["signals"])


def _load_signal_rows(
    *,
    symbol: str | None = None,
    expand_watchlist: bool = True,
) -> tuple[list[dict], str]:
    cfg = load_config()
    trading = cfg.get("trading", {})
    timeframe = str(trading.get("timeframe", "15m"))

    if expand_watchlist:
        watchlist = watchlist_symbols(cfg)
        rows = build_universe_signals(
            watchlist,
            timeframe=timeframe,
        )
        persisted = sum(1 for r in rows if r.get("pine_persisted"))
        source = (
            f"pine+kv({persisted})" if persisted else "pine+placeholder"
        )
    else:
        rows = [dict(s) for s in MOCK_SIGNALS]
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
    """List signals sorted actionable-first, then by |score| desc.

    Action is Pine-only: BUY / SELL / HOLD (worker KV when present).
    """
    rows, source = _load_signal_rows(symbol=symbol, expand_watchlist=expand_watchlist)
    for row in rows:
        act = str(row.get("action") or "HOLD").upper()
        if act in {"BUY_CE_BIAS", "BUY_CE", "LONG"}:
            row["action"] = "BUY"
        elif act in {"BUY_PE_BIAS", "BUY_PE", "SHORT"}:
            row["action"] = "SELL"
        elif act not in {"BUY", "SELL", "HOLD"}:
            row["action"] = "HOLD"
    rows = sort_signal_rows(rows)
    total = len(rows)
    page = rows[offset : offset + limit]
    # Enrich only the current page so LTP/candle lookups stay bounded.
    mark_meta = await enrich_rows_with_prices(page)
    return {
        "items": page,
        "total": total,
        "offset": offset,
        "limit": limit,
        "source": source,
        "sort": "actionable,|score|desc,confidence,symbol",
        "marks_applied": mark_meta.get("marks_applied", []),
        "price_sources": mark_meta.get("price_sources", {}),
        "note": (
            "Action = Pine BUY/SELL/HOLD only "
            "(EMA13/21 cross + next 15m candle fully clear above/below both). "
            "Hover reason_summary for Pine factor detail. "
            "price = live LTP when available, else candle close / last-known mark."
        ),
    }


@router.get("/signals/top-hybrid")
async def top_hybrid(
    limit: int = Query(5, ge=1, le=20),
    expand_watchlist: bool = Query(True),
) -> dict:
    """Top BUY and SELL Pine strip candidates (ce/pe keys kept for UI compat)."""
    rows, source = _load_signal_rows(expand_watchlist=expand_watchlist)
    for row in rows:
        act = str(row.get("action") or "HOLD").upper()
        if act in {"BUY_CE_BIAS", "BUY_CE", "LONG"}:
            row["action"] = "BUY"
        elif act in {"BUY_PE_BIAS", "BUY_PE", "SHORT"}:
            row["action"] = "SELL"
    sides = pick_top_hybrid_sides(rows, limit=limit)
    await enrich_rows_with_prices(sides["ce"])
    await enrich_rows_with_prices(sides["pe"])
    return {
        "ce": sides["ce"],
        "pe": sides["pe"],
        "limit": limit,
        "source": source,
        "note": "Top Pine BUY / SELL (Action from buy_signal / sell_signal only).",
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
            row = dict(s)
            await enrich_rows_with_prices([row])
            return {
                **row,
                "recommendation": {
                    "rationale": "BUY single-leg ATM (mock)",
                    "legs": [],
                },
                "features": {"rsi": 58.2, "ema_fast": 24490, "ema_slow": 24410},
            }
    raise HTTPException(status_code=404, detail="signal not found")
