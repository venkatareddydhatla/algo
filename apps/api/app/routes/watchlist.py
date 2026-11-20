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
    """Return the configured watchlist (volatile_fno50 or nse_fno alias)."""
    cfg = load_config()
    trading = cfg.get("trading", {})
    symbols = watchlist_symbols(cfg)
    active = set(scan_symbols(cfg))

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
        "source": "config+universe",
        "note": (
            "Worker scores the configured universe when max_active_symbols is 0/null "
            f"(active_scan={len(active)}{' unlimited' if unlimited else ''}). "
            "Batched via scan_batch_size; volatile_fno50 is ~49 equities (not full NSE F&O)."
        ),
    }
