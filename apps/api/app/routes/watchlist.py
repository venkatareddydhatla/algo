"""Watchlist / universe read APIs (paginated)."""

from __future__ import annotations

from fastapi import APIRouter, Query

from options_portal_domain.config import load_config, scan_symbols, watchlist_symbols
from options_portal_domain.universe import INDEX_SYMBOLS, load_universe_csv, resolve_universe_path

router = APIRouter(prefix="/api/v1", tags=["watchlist"])


@router.get("/watchlist")
async def list_watchlist(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    q: str | None = Query(None, description="Optional symbol substring filter"),
) -> dict:
    """Return the full configured watchlist (NSE F&O universe when universe: nse_fno)."""
    cfg = load_config()
    trading = cfg.get("trading", {})
    symbols = watchlist_symbols(cfg)
    active = set(scan_symbols(cfg))

    meta_by_sym: dict[str, dict] = {}
    try:
        path = resolve_universe_path("nse_fno", config_dir=None)
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
        items.append(
            {
                "symbol": sym,
                "name": meta.get("name") or sym,
                "kind": kind,
                "expiry_rule": meta.get("expiry_rule")
                or ("weekly" if kind == "index" else "monthly"),
                "lot_size": meta.get("lot_size"),
                "active_scan": sym in active,
            }
        )

    return {
        "items": items,
        "total": total,
        "offset": offset,
        "limit": limit,
        "universe": trading.get("universe") or trading.get("symbols"),
        "max_active_symbols": trading.get("max_active_symbols", 20),
        "active_scan_count": len(active),
        "source": "config+universe",
        "note": (
            "Portal watches the full list. Worker/TA scans only max_active_symbols "
            "(default 20) in batches to limit CPU — raise carefully."
        ),
    }
