"""Trades / P&L read API — equity dry-run paper ledger (paginated closed trades)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.mock_data import MOCK_TRADES
from options_portal_domain.config import paper_retention_config, trading_mode
from options_portal_domain.round_trips import (
    pair_round_trip_trades,
    sort_closed_trades_newest_first,
)

router = APIRouter(prefix="/api/v1", tags=["trades"])


@router.get("/trades")
async def trades_summary(
    offset: Annotated[
        int, Query(ge=0, description="Closed-trades page offset (full history)")
    ] = 0,
    limit: Annotated[
        int, Query(ge=1, le=200, description="Closed-trades page size")
    ] = 50,
) -> dict:
    """Return open positions + paginated **round-trip** closed trades.

    ``fills`` / ``closed_trades`` are paired entry+exit rows (LONG/SHORT), matching
    Backtest Fills and the daily summary email. Open legs stay under ``positions``.
    """
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
    raw = list(MOCK_TRADES.get("fills") or [])
    closed = sort_closed_trades_newest_first(pair_round_trip_trades(raw))
    # Mock may only have unpaired legs — surface as empty closed set rather than invent
    page = closed[offset : offset + limit]
    return {
        **MOCK_TRADES,
        "mode": mode,
        "instrument": "equity",
        "execution": "dry_run",
        "dry_run": True,
        "source": "mock",
        "fills": page,
        "fills_kind": "round_trip",
        "fills_total": len(closed),
        "fills_offset": offset,
        "fills_limit": limit,
        "closed_trades": page,
        "closed_trades_total": len(closed),
        "raw_fills_total": len(raw),
        "retention": {
            "auto_purge": False,
            "history_scope": "all_fills",
            **retention,
        },
        "note": "Paper ledger unavailable — showing mock. Use PERSISTENCE_PROFILE=local.",
    }
