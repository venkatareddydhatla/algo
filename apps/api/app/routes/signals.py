"""Signal read APIs (mock-backed until persistence wired)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.marks import enrich_rows_with_prices
from app.mock_data import (
    MOCK_SIGNALS,
    apply_open_trade_display_actions,
    build_universe_signals,
    clear_persisted_pine_signals,
    rows_for_top_strip,
)
from options_portal_domain.config import load_config, watchlist_symbols
from options_portal_domain.equity import (
    filter_rows_below_min_equity_price,
    min_equity_price_from_config,
)
from options_portal_hybrid.explain import (
    pick_top_hybrid_sides,
    sort_signal_rows,
)

router = APIRouter(prefix="/api/v1", tags=["signals"])


def _normalize_pine_actions(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        act = str(row.get("action") or "HOLD").upper()
        if act in {"BUY_CE_BIAS", "BUY_CE", "LONG"}:
            row["action"] = "BUY"
        elif act in {"BUY_PE_BIAS", "BUY_PE", "SHORT"}:
            row["action"] = "SELL"
        elif act not in {"BUY", "SELL", "HOLD"}:
            row["action"] = "HOLD"
        # Keep pine_action aligned when present (before open-trade overlay).
        if "pine_action" in row:
            pine = str(row.get("pine_action") or "HOLD").upper()
            if pine in {"BUY_CE_BIAS", "BUY_CE", "LONG"}:
                row["pine_action"] = "BUY"
            elif pine in {"BUY_PE_BIAS", "BUY_PE", "SHORT"}:
                row["pine_action"] = "SELL"
            elif pine not in {"BUY", "SELL", "HOLD"}:
                row["pine_action"] = "HOLD"


async def _apply_min_equity_price_filter(
    rows: list[dict[str, Any]],
    *,
    cfg: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Enrich marks, drop LTP/close &lt; min_equity_price, clear stale Pine KV."""
    config = cfg or load_config()
    min_px = min_equity_price_from_config(config)
    mark_meta = await enrich_rows_with_prices(rows)
    kept, dropped = filter_rows_below_min_equity_price(rows, min_price=min_px)
    cleared: list[str] = []
    if dropped:
        cleared = clear_persisted_pine_signals(
            [str(r.get("symbol") or "") for r in dropped]
        )
    meta = {
        **mark_meta,
        "min_equity_price": min_px,
        "price_floor_dropped": sorted(
            {str(r.get("symbol") or "").upper() for r in dropped if r.get("symbol")}
        ),
        "pine_kv_cleared": cleared,
    }
    return kept, meta


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

    User-facing Action is Pine BUY/SELL/HOLD, except while a dry-run position is
    open: LONG → BUY, SHORT → SELL (display / mode only). ``pine_action`` keeps
    expire-stale signal freshness. Entries still require a fresh Pine bar.
    Symbols with LTP/close below ``min_equity_price`` (₹20) are omitted.
    """
    cfg = load_config()
    rows, source = _load_signal_rows(symbol=symbol, expand_watchlist=expand_watchlist)
    _normalize_pine_actions(rows)
    rows, floor_meta = await _apply_min_equity_price_filter(rows, cfg=cfg)
    apply_open_trade_display_actions(rows)
    rows = sort_signal_rows(rows)
    total = len(rows)
    page = rows[offset : offset + limit]
    sticky = sum(1 for r in page if r.get("action_from_open_trade"))
    return {
        "items": page,
        "total": total,
        "offset": offset,
        "limit": limit,
        "source": source,
        "sort": "actionable,|score|desc,confidence,symbol",
        "marks_applied": floor_meta.get("marks_applied", []),
        "price_sources": floor_meta.get("price_sources", {}),
        "min_equity_price": floor_meta.get("min_equity_price"),
        "price_floor_dropped": floor_meta.get("price_floor_dropped", []),
        "open_trade_action_overlay": sticky,
        "note": (
            "Action = Pine BUY/SELL/HOLD "
            "(EMA13/21 cross + next 15m candle fully clear above/below both), "
            "or sticky BUY/SELL while that symbol has an open dry-run trade "
            "(LONG→BUY, SHORT→SELL; display only — entries need a fresh Pine bar). "
            "pine_action keeps signal freshness after expire-stale. "
            "Hover reason_summary for Pine factor detail. "
            "price = live LTP when available, else candle close / last-known mark. "
            f"Symbols below ₹{float(floor_meta.get('min_equity_price') or 20):g} "
            "are excluded from watchlist / TOP strips."
        ),
    }


@router.get("/signals/top-hybrid")
async def top_hybrid(
    limit: int = Query(5, ge=1, le=20),
    expand_watchlist: bool = Query(True),
) -> dict:
    """Top BUY and SELL Pine strip candidates (ce/pe keys kept for UI compat).

    Strip ranking uses ``pine_action`` (fresh signal), not sticky open-trade Action.
    """
    cfg = load_config()
    rows, source = _load_signal_rows(expand_watchlist=expand_watchlist)
    _normalize_pine_actions(rows)
    # Enrich + price-floor filter BEFORE picking TOP BUY/SELL so IDEA-like
    # sub-₹20 names cannot occupy the strip from stale Pine KV.
    rows, floor_meta = await _apply_min_equity_price_filter(rows, cfg=cfg)
    # Capture pine before optional overlay; strip must not treat open LONGs as
    # fresh BUY signals.
    apply_open_trade_display_actions(rows)
    sides = pick_top_hybrid_sides(rows_for_top_strip(rows), limit=limit)
    return {
        "ce": sides["ce"],
        "pe": sides["pe"],
        "limit": limit,
        "source": source,
        "min_equity_price": floor_meta.get("min_equity_price"),
        "price_floor_dropped": floor_meta.get("price_floor_dropped", []),
        "note": (
            "Top Pine BUY / SELL from fresh pine_action only "
            "(sticky open-trade Action does not enter the strip). "
            f"Excludes symbols below ₹{float(floor_meta.get('min_equity_price') or 20):g}."
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
            row = dict(s)
            _normalize_pine_actions([row])
            apply_open_trade_display_actions([row])
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
