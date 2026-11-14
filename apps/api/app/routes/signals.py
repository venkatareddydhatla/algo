"""Signal read APIs (mock-backed until persistence wired)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query

from app.mock_data import MOCK_SIGNALS
from options_portal_domain.config import load_config, watchlist_symbols

router = APIRouter(prefix="/api/v1", tags=["signals"])


@router.get("/signals")
async def list_signals(
    symbol: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    expand_watchlist: bool = Query(
        True,
        description="Pad HOLD stubs for watchlist symbols without a mock signal",
    ),
) -> dict:
    """List signals. When expand_watchlist is true, include HOLD rows for the full universe page."""
    by_sym = {s["symbol"].upper(): s for s in MOCK_SIGNALS}
    rows = list(MOCK_SIGNALS)

    if expand_watchlist:
        cfg = load_config()
        watchlist = watchlist_symbols(cfg)
        now = datetime.now(tz=timezone.utc).isoformat()
        for sym in watchlist:
            if sym in by_sym:
                continue
            rows.append(
                {
                    "id": f"watch-{sym.lower()}",
                    "symbol": sym,
                    "timeframe": cfg.get("trading", {}).get("timeframe", "15m"),
                    "action": "HOLD",
                    "hybrid_score": 0.0,
                    "tech_score": 0.0,
                    "news_score": 0.0,
                    "confidence": 0.0,
                    "expiry_hint": "weekly"
                    if sym in {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "NIFTYFPI"}
                    else "monthly",
                    "updated_at": now,
                    "reasons": ["Awaiting active scan / no signal yet"],
                    "stub": True,
                }
            )

    if symbol:
        rows = [s for s in rows if s["symbol"].upper() == symbol.upper()]

    total = len(rows)
    page = rows[offset : offset + limit]
    return {
        "items": page,
        "total": total,
        "offset": offset,
        "limit": limit,
        "source": "mock+watchlist" if expand_watchlist else "mock",
    }


@router.get("/signals/{signal_id}")
async def get_signal(signal_id: str) -> dict:
    for s in MOCK_SIGNALS:
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
