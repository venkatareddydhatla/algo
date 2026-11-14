"""Signal read APIs (mock-backed until persistence wired)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.mock_data import MOCK_SIGNALS

router = APIRouter(prefix="/api/v1", tags=["signals"])


@router.get("/signals")
async def list_signals(symbol: str | None = None) -> dict:
    rows = MOCK_SIGNALS
    if symbol:
        rows = [s for s in rows if s["symbol"].upper() == symbol.upper()]
    return {"items": rows, "source": "mock"}


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
