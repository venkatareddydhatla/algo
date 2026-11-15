"""Trades / P&L read API — equity dry-run paper ledger (paginated history)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.mock_data import MOCK_TRADES
from options_portal_domain.config import paper_retention_config, trading_mode

router = APIRouter(prefix="/api/v1", tags=["trades"])


@router.get("/trades")
async def trades_summary(
    offset: Annotated[int, Query(ge=0, description="Fills page offset (full history)")] = 0,
    limit: Annotated[int, Query(ge=1, le=200, description="Fills page size")] = 50,
) -> dict:
    mode = trading_mode()
    safe_mode = "dry_run" if mode == "live" else mode
    retention = paper_retention_config()
    try:
        from options_portal_persistence.paper_trades import PaperTradeStore

        store = PaperTradeStore()
        if store.available:
            payload = store.summary(
                mode=safe_mode,  # type: ignore[arg-type]
                fills_limit=limit,
                fills_offset=offset,
            )
            payload["requested_mode"] = mode
            payload["retention"] = {
                **payload.get("retention", {}),
                **retention,
            }
            return payload
    except Exception:  # noqa: BLE001
        pass

    # Fallback mock when SQLite ledger unavailable (Compose without local profile)
    fills = list(MOCK_TRADES.get("fills") or [])
    page = fills[offset : offset + limit]
    return {
        **MOCK_TRADES,
        "mode": mode,
        "instrument": "equity",
        "execution": "dry_run",
        "dry_run": True,
        "source": "mock",
        "fills": page,
        "fills_total": len(fills),
        "fills_offset": offset,
        "fills_limit": limit,
        "retention": {
            "auto_purge": False,
            "history_scope": "all_fills",
            **retention,
        },
        "note": "Paper ledger unavailable — showing mock. Use PERSISTENCE_PROFILE=local.",
    }
