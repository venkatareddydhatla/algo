"""Trades / P&L read API — equity dry-run paper ledger."""

from __future__ import annotations

from fastapi import APIRouter

from app.mock_data import MOCK_TRADES
from options_portal_domain.config import trading_mode

router = APIRouter(prefix="/api/v1", tags=["trades"])


@router.get("/trades")
async def trades_summary() -> dict:
    mode = trading_mode()
    safe_mode = "dry_run" if mode == "live" else mode
    try:
        from options_portal_persistence.paper_trades import PaperTradeStore

        store = PaperTradeStore()
        if store.available:
            payload = store.summary(mode=safe_mode)  # type: ignore[arg-type]
            payload["requested_mode"] = mode
            return payload
    except Exception:  # noqa: BLE001
        pass

    # Fallback mock when SQLite ledger unavailable (Compose without local profile)
    return {
        **MOCK_TRADES,
        "mode": mode,
        "instrument": "equity",
        "execution": "dry_run",
        "dry_run": True,
        "source": "mock",
        "note": "Paper ledger unavailable — showing mock. Use PERSISTENCE_PROFILE=local.",
    }
