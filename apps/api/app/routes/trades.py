"""Trades / P&L read API — equity dry-run paper ledger (paginated closed trades)."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Query

from app.mock_data import MOCK_TRADES
from options_portal_domain.config import paper_retention_config, trading_mode
from options_portal_domain.round_trips import (
    pair_round_trip_trades,
    sort_closed_trades_newest_first,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["trades"])


async def _live_marks_for_symbols(symbols: list[str]) -> dict[str, float]:
    """Best-effort per-symbol Upstox LTP map for open-position uPnL (never raises).

    Keys are uppercased tradingsymbols. ``get_ltp`` / ``get_ltp_map`` match each
    instrument_key in the response — never reuse one quote across symbols.
    """
    from app.marks import live_ltp_map

    marks = await live_ltp_map(symbols)
    missing = sorted({str(s).upper() for s in symbols if s} - set(marks))
    for sym in missing:
        logger.warning(
            "trades uPnL: no LTP for %s — leaving uPnL 0",
            sym,
        )
    return marks


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
    Open-position uPnL uses live LTP marks when market data is available.
    """
    mode = trading_mode()
    safe_mode = "dry_run" if mode == "live" else mode
    retention = paper_retention_config()
    try:
        from options_portal_persistence.paper_trades import PaperTradeStore

        store = PaperTradeStore()
        if store.available:
            open_syms = store.open_symbols(mode=safe_mode)  # type: ignore[arg-type]
            marks = await _live_marks_for_symbols(list(open_syms))
            payload: dict[str, Any] = store.summary(
                mode=safe_mode,  # type: ignore[arg-type]
                fills_limit=limit,
                fills_offset=offset,
                marks=marks or None,
            )
            payload["requested_mode"] = mode
            payload["marks_applied"] = sorted(marks.keys())
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
