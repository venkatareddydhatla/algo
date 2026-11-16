"""Trades / P&L read API — equity dry-run paper ledger (paginated closed trades)."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Query

from app.mock_data import MOCK_TRADES
from options_portal_domain.calendar import pnl_ist_day
from options_portal_domain.config import paper_retention_config, trading_mode
from options_portal_domain.round_trips import (
    closed_trades_for_ist_day,
    pair_round_trip_trades,
    sort_closed_trades_newest_first,
    sum_closed_realized_pnl,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["trades"])


async def _open_position_marks(
    symbols: list[str],
) -> tuple[dict[str, float], dict[str, str]]:
    """Per-symbol live LTP for open uPnL, retaining last-known on tick gaps.

    Keys are uppercased tradingsymbols. Never reuses one quote across symbols.
    A partial Upstox failure must not zero marks for symbols that already have
    a retained live LTP.
    """
    from app.marks import open_position_marks_for_symbols

    return await open_position_marks_for_symbols(symbols)


@router.get("/trades")
async def trades_summary(
    offset: Annotated[
        int, Query(ge=0, description="Closed-trades page offset (full history)")
    ] = 0,
    limit: Annotated[
        int, Query(ge=1, le=200, description="Closed-trades page size")
    ] = 50,
    day: Annotated[
        str | None,
        Query(
            description=(
                "Optional IST day YYYY-MM-DD for Day realized. "
                "Default: NSE cash session day (rolls at next 09:15 IST open)."
            ),
        ),
    ] = None,
) -> dict:
    """Return open positions + paginated **round-trip** closed trades.

    ``fills`` / ``closed_trades`` are paired entry+exit rows (LONG/SHORT), matching
    Backtest Fills and the daily summary email. Open legs stay under ``positions``.
    Open-position uPnL uses live LTP when the Upstox token is present; tick gaps
    keep the last-known live mark instead of zeroing the whole table.

    ``day_realized`` is the sum of closed round-trip PnL for the NSE session IST
    day (``day`` query or default ``pnl_ist_day``), not open marks and not UTC
    midnight.
    """
    mode = trading_mode()
    safe_mode = "dry_run" if mode == "live" else mode
    retention = paper_retention_config()
    day_ist = (day or "").strip() or None
    if day_ist and len(day_ist) >= 10:
        day_ist = day_ist[:10]
    try:
        from options_portal_persistence.paper_trades import PaperTradeStore

        store = PaperTradeStore()
        if store.available:
            open_syms = store.open_symbols(mode=safe_mode)  # type: ignore[arg-type]
            marks, mark_sources = await _open_position_marks(list(open_syms))
            payload: dict[str, Any] = store.summary(
                mode=safe_mode,  # type: ignore[arg-type]
                fills_limit=limit,
                fills_offset=offset,
                marks=marks,
                mark_sources=mark_sources,
                day_ist=day_ist,
            )
            payload["requested_mode"] = mode
            payload["marks_applied"] = sorted(
                s for s, src in mark_sources.items() if src == "ltp"
            )
            payload["marks_last_known"] = sorted(
                s for s, src in mark_sources.items() if src == "last_known"
            )
            payload["mark_gaps"] = sorted(
                {
                    str(s).upper()
                    for s in open_syms
                    if s and str(s).upper() not in marks
                }
            )
            payload["retention"] = {
                **payload.get("retention", {}),
                **retention,
            }
            return payload
    except Exception:  # noqa: BLE001
        logger.exception("trades summary paper-ledger path failed")

    # Fallback mock when SQLite ledger unavailable (Compose without local profile)
    raw = list(MOCK_TRADES.get("fills") or [])
    closed = sort_closed_trades_newest_first(pair_round_trip_trades(raw))
    # Mock may only have unpaired legs — surface as empty closed set rather than invent
    page = closed[offset : offset + limit]
    mock_day = day_ist or pnl_ist_day().isoformat()
    mock_day_realized = sum_closed_realized_pnl(
        closed_trades_for_ist_day(closed, mock_day)
    )
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
        "day_ist": mock_day,
        "day_realized": mock_day_realized,
        "day_unrealized": float(MOCK_TRADES.get("day_unrealized") or 0.0),
        "retention": {
            "auto_purge": False,
            "history_scope": "all_fills",
            **retention,
        },
        "note": "Paper ledger unavailable — showing mock. Use PERSISTENCE_PROFILE=local.",
    }
