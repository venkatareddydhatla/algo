"""Watchlist / universe read APIs + custom equity append/remove."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from options_portal_broker.instruments import resolve_instrument_key
from options_portal_domain.config import load_config, scan_symbols, watchlist_symbols
from options_portal_domain.custom_watchlist import (
    add_custom_symbol,
    load_custom_symbols,
    remove_custom_symbol,
)
from options_portal_domain.universe import INDEX_SYMBOLS, load_universe_csv, resolve_universe_path

router = APIRouter(prefix="/api/v1", tags=["watchlist"])


class CustomSymbolBody(BaseModel):
    """POST body: NSE equity tradingsymbol to append on top of curated list."""

    symbol: str = Field(..., min_length=1, max_length=24)


def _base_and_custom(cfg: dict) -> tuple[list[str], list[str], set[str]]:
    trading = cfg.get("trading") or {}
    base = [str(s).upper() for s in (trading.get("symbols_base") or [])]
    if not base:
        # Fallback when older config path lacked symbols_base
        from options_portal_domain.universe import resolve_watchlist_symbols

        base = resolve_watchlist_symbols(trading)
    # Prefer live KV so UI sees writes without process restart.
    customs = load_custom_symbols()
    return base, customs, set(customs)


@router.get("/watchlist")
async def list_watchlist(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    q: str | None = Query(None, description="Optional symbol substring filter"),
) -> dict:
    """Return curated TradingView list + persisted custom equities (deduped)."""
    cfg = load_config()
    trading = cfg.get("trading", {})
    symbols = watchlist_symbols(cfg)
    active = set(scan_symbols(cfg))
    base, customs, custom_set = _base_and_custom(cfg)
    base_set = set(base)

    meta_by_sym: dict[str, dict] = {}
    try:
        universe_spec = trading.get("universe") or trading.get("symbols") or "volatile_fno50"
        if isinstance(universe_spec, list):
            universe_spec = "volatile_fno50"
        path = resolve_universe_path(str(universe_spec), config_dir=None)
        if path and path.exists():
            for u in load_universe_csv(path):
                meta_by_sym[u.symbol] = {
                    "name": u.name,
                    "kind": u.kind,
                    "expiry_rule": u.expiry_rule,
                    "lot_size": u.lot_size,
                }
    except OSError:
        pass

    if q:
        needle = q.strip().upper()
        symbols = [s for s in symbols if needle in s]

    total = len(symbols)
    page = symbols[offset : offset + limit]
    items = []
    for sym in page:
        meta = meta_by_sym.get(sym, {})
        kind = meta.get("kind") or ("index" if sym in INDEX_SYMBOLS else "equity")
        on_custom = sym in custom_set
        on_base = sym in base_set
        items.append(
            {
                "symbol": sym,
                "name": meta.get("name") or sym,
                "kind": kind,
                "expiry_rule": meta.get("expiry_rule")
                or ("weekly" if kind == "index" else "monthly"),
                "lot_size": meta.get("lot_size"),
                "active_scan": sym in active,
                "source": "custom" if (on_custom and not on_base) else "curated",
                "custom": bool(on_custom and not on_base),
            }
        )

    max_active = trading.get("max_active_symbols", 0)
    unlimited = max_active is None or int(max_active or 0) <= 0
    return {
        "items": items,
        "total": total,
        "offset": offset,
        "limit": limit,
        "universe": trading.get("universe") or trading.get("symbols"),
        "max_active_symbols": max_active,
        "active_scan_count": len(active),
        "scan_batch_size": trading.get("scan_batch_size_resolved")
        or trading.get("scan_batch_size", 8),
        "base_count": len(base),
        "custom_count": len(customs),
        "custom_symbols": customs,
        "source": "config+universe+custom_kv",
        "note": (
            "Worker scores curated TradingView watchlist plus portal custom equities "
            f"(base={len(base)} custom={len(customs)} active_scan={len(active)}"
            f"{' unlimited' if unlimited else ''}). "
            "Customs persist in SQLite kv_store; duplicates of curated names are skipped."
        ),
    }


@router.get("/watchlist/custom")
async def list_custom_watchlist() -> dict:
    """List only user-appended equity symbols (SQLite kv)."""
    cfg = load_config()
    base, customs, _ = _base_and_custom(cfg)
    active = set(scan_symbols(cfg))
    items = []
    for sym in customs:
        ikey = resolve_instrument_key(sym)
        items.append(
            {
                "symbol": sym,
                "instrument_key": ikey,
                "active_scan": sym in active,
                "on_curated": sym in set(base),
                "source": "custom",
            }
        )
    return {
        "items": items,
        "total": len(items),
        "base_count": len(base),
        "source": "sqlite_kv",
        "kv_key": "watchlist.custom_equity_symbols",
        "note": (
            "Add via POST /api/v1/watchlist/custom; remove via DELETE "
            "/api/v1/watchlist/custom/{symbol}. Curated CSV is never edited."
        ),
    }


@router.post("/watchlist/custom")
async def append_custom_watchlist(body: CustomSymbolBody) -> dict:
    """Append one NSE equity ticker; joins the same 15m bar-close full scan."""
    cfg = load_config()
    base, _, _ = _base_and_custom(cfg)
    sym = body.symbol.strip().upper()
    ikey = resolve_instrument_key(sym)
    result = add_custom_symbol(
        sym,
        instrument_key=ikey,
        base_watchlist=base,
    )
    if not result.get("ok") and result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("message") or "rejected")

    # Refresh merged counts for the response
    cfg2 = load_config()
    merged = watchlist_symbols(cfg2)
    customs = load_custom_symbols()
    return {
        **result,
        "watchlist_total": len(merged),
        "custom_symbols": customs,
        "active_scan_count": len(scan_symbols(cfg2)),
        "note": (
            "Custom equities join the next 15m bar-close full scan with the same "
            "gates (min ₹40, 120m cooloff, ±2% exits, profit_candle_protect, "
            "dry_run, max 10 opens, 09:30–15:20). Indices rejected."
        ),
    }


@router.delete("/watchlist/custom/{symbol}")
async def delete_custom_watchlist(symbol: str) -> dict:
    """Remove a portal-added custom equity (curated CSV names cannot be deleted)."""
    result = remove_custom_symbol(symbol)
    if not result.get("ok"):
        status = result.get("status")
        code = 404 if status == "not_custom" else 400
        raise HTTPException(status_code=code, detail=result.get("message") or "rejected")

    cfg = load_config()
    return {
        **result,
        "watchlist_total": len(watchlist_symbols(cfg)),
        "active_scan_count": len(scan_symbols(cfg)),
    }
