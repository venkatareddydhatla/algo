"""Trades / P&L API — equity dry-run paper ledger (read + manual close)."""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel, Field

from app.mock_data import MOCK_TRADES
from options_portal_domain.calendar import pnl_ist_day
from options_portal_domain.config import (
    is_halted,
    load_config,
    paper_retention_config,
    trading_mode,
)
from options_portal_domain.equity import plan_equity_intents, resolve_equity_mark
from options_portal_domain.round_trips import (
    closed_trades_for_ist_day,
    pair_round_trip_trades,
    sort_closed_trades_newest_first,
    sum_closed_realized_pnl,
)
from options_portal_domain.types import SignalAction

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["trades"])

# Manual close is paper/dry_run only — never broker live placement.
_MANUAL_CLOSE_REASON = "manual_close"
# Keep close snappy — never block the UI on slow Upstox candle history.
_CLOSE_MARK_TIMEOUT_SEC = 4.0


class ManualCloseBody(BaseModel):
    """Body for POST /trades/{symbol}/close (JSON)."""

    reason: str = Field(default=_MANUAL_CLOSE_REASON, min_length=1, max_length=120)
    confirm: bool = True


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


def _paper_safe_mode() -> str:
    """Ledger mode for paper closes: live → dry_run; demo stays demo."""
    mode = trading_mode()
    return "dry_run" if mode == "live" else mode


async def _close_fill_mark(
    sym: str,
    *,
    entry_avg: float,
) -> tuple[Any, dict[str, str]]:
    """Resolve a close mark without hanging the UI on slow MD.

    Prefer live/last-known LTP (short timeout). Skip candle history when LTP
    is present — historical candle fetch was blocking manual close on Windows.
    Fall back to entry avg so the SQLite exit always completes.
    """
    marks: dict[str, float] = {}
    mark_sources: dict[str, str] = {}
    try:
        marks, mark_sources = await asyncio.wait_for(
            _open_position_marks([sym]),
            timeout=_CLOSE_MARK_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "manual_close %s LTP timed out after %.1fs — using entry/fallback",
            sym,
            _CLOSE_MARK_TIMEOUT_SEC,
        )
    except Exception:  # noqa: BLE001
        logger.warning("manual_close %s LTP failed", sym, exc_info=True)

    candle_px: float | None = None
    if sym not in marks:
        try:
            from app.marks import candle_close_map

            candle = await asyncio.wait_for(
                candle_close_map([sym]),
                timeout=_CLOSE_MARK_TIMEOUT_SEC,
            )
            if candle.get(sym):
                candle_px = float(candle[sym])
        except asyncio.TimeoutError:
            logger.warning("manual_close %s candle mark timed out", sym)
        except Exception:  # noqa: BLE001
            logger.warning("manual_close %s candle mark failed", sym, exc_info=True)

    mark = resolve_equity_mark(
        ltp=marks.get(sym),
        candle_close=candle_px,
        entry_avg=entry_avg if entry_avg > 0 else None,
    )
    if mark.source != "ltp":
        logger.warning(
            "manual_close %s mark source=%s detail=%s px=%.4f",
            sym,
            mark.source,
            mark.detail,
            mark.price,
        )
    return mark, mark_sources


@router.post("/trades/{symbol}/close")
async def close_open_position(
    symbol: str,
    body: ManualCloseBody = Body(default_factory=ManualCloseBody),
) -> dict[str, Any]:
    """Close one open dry_run/demo paper position immediately (manual Sell/Close).

    Long → SELL; short → BUY cover. Reason defaults to ``manual_close``.
    Never places a live broker order (v1 dry_run only).
    """
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail="confirm=true required to close an open paper position",
        )

    # Path may arrive URL-encoded (e.g. odd symbols); normalize once.
    from urllib.parse import unquote

    sym = unquote(str(symbol or "")).strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol required")

    if is_halted():
        raise HTTPException(
            status_code=403,
            detail="Trading halt active — manual close blocked",
        )

    reason = (body.reason or _MANUAL_CLOSE_REASON).strip() or _MANUAL_CLOSE_REASON
    safe_mode = _paper_safe_mode()
    if safe_mode not in {"dry_run", "demo"}:
        raise HTTPException(
            status_code=403,
            detail="Manual close is dry_run/demo paper only",
        )

    from options_portal_persistence.paper_trades import PaperTradeStore

    store = PaperTradeStore()
    if not store.available:
        raise HTTPException(
            status_code=503,
            detail="Paper ledger unavailable (set PERSISTENCE_PROFILE=local)",
        )

    pos = store.get_position(sym, mode=safe_mode)  # type: ignore[arg-type]
    if not pos:
        raise HTTPException(
            status_code=404,
            detail=f"No open paper position for {sym}",
        )

    open_side = str(pos.get("side") or "").strip().lower()
    if open_side not in {"long", "short"}:
        raise HTTPException(
            status_code=409,
            detail=f"Open position side invalid for {sym}: {open_side!r}",
        )
    open_qty = int(pos.get("qty") or 0)
    if open_qty <= 0:
        raise HTTPException(
            status_code=409,
            detail=f"Open position qty invalid for {sym}",
        )
    entry_avg = float(pos.get("avg_price") or 0.0)

    logger.info(
        "manual_close start symbol=%s side=%s qty=%d mode=%s",
        sym,
        open_side,
        open_qty,
        safe_mode,
    )

    mark, mark_sources = await _close_fill_mark(sym, entry_avg=entry_avg)

    intents = plan_equity_intents(
        sym,
        SignalAction.EXIT,
        open_side=open_side,  # type: ignore[arg-type]
        open_qty=open_qty,
        qty=open_qty,
        price=mark.price,
        reason=reason,
    )
    if not intents:
        raise HTTPException(
            status_code=409,
            detail=f"No close intent for {sym} (already flat?)",
        )
    intent = intents[0]

    cfg = load_config()
    risk_cfg = dict(cfg.get("risk") or {})
    trading_risk = dict((cfg.get("trading") or {}).get("risk") or {})
    # Prefer trading.risk (local.yaml); fall back to top-level risk.
    merged_risk = {**risk_cfg, **trading_risk}
    equity_cfg = (cfg.get("trading") or {}).get("equity") or {}
    share_qty = int(
        equity_cfg.get("share_qty", merged_risk.get("equity_share_qty", 1)) or 1
    )
    limits = {
        **merged_risk,
        "equity_share_qty": share_qty,
        "max_equity_qty_per_symbol": int(
            merged_risk.get("max_equity_qty_per_symbol", share_qty) or share_qty
        ),
        "max_open_symbols": int(
            equity_cfg.get(
                "max_open_symbols", merged_risk.get("max_open_symbols", 10)
            )
            or 10
        ),
        # Explicit user close must not be blocked by day-loss entry caps.
        "max_day_loss_inr": 1e18,
        "halt": bool((cfg.get("trading") or {}).get("halt")),
    }

    from options_portal_risk.order_router import SafeOrderRouter

    portfolio = store.portfolio_snapshot(mode=safe_mode)  # type: ignore[arg-type]
    router = SafeOrderRouter()
    order = await router.execute_equity(
        intent,
        safe_mode,  # type: ignore[arg-type]
        actor_user_id="portal-manual-close",
        portfolio=portfolio,
        limits=limits,
    )
    if order.status != "simulated":
        raise HTTPException(
            status_code=409,
            detail=order.message or f"Close rejected ({order.status})",
        )

    persist_meta = store.record_simulated_fill(
        intent, order, mode=safe_mode  # type: ignore[arg-type]
    )
    if not persist_meta.get("persisted"):
        raise HTTPException(
            status_code=409,
            detail=str(persist_meta.get("reason") or "Close not persisted"),
        )

    # Same fill-notify path as worker (Gmail when configured).
    try:
        from options_portal_notify.factory import build_gmail_notifier

        gmail = build_gmail_notifier(cfg)
        if gmail is not None and gmail.enabled:
            gmail.send_fill(
                {
                    "symbol": intent.symbol,
                    "side": intent.side,
                    "qty": intent.qty,
                    "price": intent.limit_price,
                    "reason": intent.reason,
                    "action_kind": intent.action_kind,
                    "ts_ist_display": persist_meta.get("ts_ist_display"),
                },
                mode=str(safe_mode),
            )
    except Exception:  # noqa: BLE001
        logger.exception("Gmail fill notify failed for manual_close %s", sym)

    still_open = store.get_position(sym, mode=safe_mode)  # type: ignore[arg-type]
    if still_open is not None:
        logger.error(
            "manual_close %s persisted fill but position still open — ledger bug",
            sym,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Close fill recorded but {sym} still open in SQLite",
        )

    logger.info(
        "manual_close ok symbol=%s side=%s order=%s px=%.4f pnl=%s",
        sym,
        intent.side,
        intent.side,
        float(intent.limit_price),
        persist_meta.get("realized_pnl"),
    )
    return {
        "ok": True,
        "symbol": sym,
        "mode": safe_mode,
        "dry_run": True,
        "instrument": "equity",
        "execution": "dry_run",
        "reason": reason,
        "closed_side": open_side,
        "order_side": intent.side,
        "qty": intent.qty,
        "price": intent.limit_price,
        "mark_source": mark.source,
        "mark_sources": mark_sources,
        "realized_pnl": persist_meta.get("realized_pnl"),
        "ts_ist_display": persist_meta.get("ts_ist_display"),
        "client_order_id": order.client_order_id,
        "broker_order_id": order.broker_order_id,
        "message": order.message,
        "position_remaining": None,
        "fill": persist_meta,
    }
