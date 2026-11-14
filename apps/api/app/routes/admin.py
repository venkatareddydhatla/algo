"""Admin config placeholders — live enablement stays gated."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.mock_data import MOCK_CONFIG
from options_portal_domain.config import load_config, scan_symbols, watchlist_symbols

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class ModeUpdate(BaseModel):
    mode: str = Field(pattern="^(dry_run|demo|live)$")
    confirm_live: bool = False


@router.get("/config")
async def get_config() -> dict:
    cfg = load_config()
    trading = dict(cfg.get("trading", MOCK_CONFIG["trading"]))
    watchlist = watchlist_symbols(cfg)
    active = scan_symbols(cfg)
    trading["symbols_watchlist_count"] = len(watchlist)
    trading["symbols_active_count"] = len(active)
    trading["symbols_active"] = active
    # Avoid dumping 200+ names into a single line in admin by default;
    # clients can call /api/v1/watchlist for the full paginated list.
    trading["symbols_preview"] = watchlist[:40]
    trading["symbols"] = watchlist  # resolved list for compatibility
    return {
        "trading": trading,
        "hybrid": cfg.get("hybrid", MOCK_CONFIG["hybrid"]),
        "source": "config+defaults",
        "v1_note": "Live order placement remains blocked regardless of mode switch",
        "universe_note": (
            f"Watchlist {len(watchlist)} symbols (universe={trading.get('universe')}); "
            f"active scan {len(active)} "
            f"(max_active_symbols={trading.get('max_active_symbols', 0)}; "
            f"0/null=unlimited). Batch={trading.get('scan_batch_size_resolved') or trading.get('scan_batch_size', 8)}; "
            f"expect ~2-5 min/full dry-run pass on one machine."
        ),
    }


@router.post("/trading-mode")
async def set_trading_mode(body: ModeUpdate) -> dict:
    """Record intended mode. Does NOT unlock live broker placement in v1."""
    if body.mode == "live" and not body.confirm_live:
        raise HTTPException(
            status_code=400,
            detail="Live mode requires confirm_live=true; placement still gated in v1",
        )
    # TODO: persist to app_config_versions + audit_events
    return {
        "accepted_mode": body.mode,
        "persisted": False,
        "live_placement_enabled": False,
        "message": (
            "Mode preference noted. v1 OrderRouter still blocks live placement. "
            "Use dry_run for all paper execution."
        ),
    }


@router.post("/halt")
async def set_halt(halt: bool = True) -> dict:
    # TODO: set TRADING_HALT via config store
    return {
        "halt": halt,
        "persisted": False,
        "message": "Kill switch stub — set TRADING_HALT env for process-level halt",
    }
