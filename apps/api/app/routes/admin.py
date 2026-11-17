"""Admin config placeholders — live enablement stays gated."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.mock_data import MOCK_CONFIG
from options_portal_domain.config import (
    load_config,
    paper_retention_config,
    scan_symbols,
    trading_mode,
    watchlist_symbols,
)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class ModeUpdate(BaseModel):
    mode: str = Field(pattern="^(dry_run|demo|live)$")
    confirm_live: bool = False


class PaperLedgerPurge(BaseModel):
    """Explicit purge/archive of paper fills before starting live orders."""

    confirm: bool = False
    archive: bool = True
    clear_positions: bool = True
    closed_only: bool = False
    mode: str = Field(default="dry_run", pattern="^(dry_run|demo)$")


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


@router.post("/paper-ledger/purge")
async def purge_paper_ledger(body: PaperLedgerPurge) -> dict:
    """Archive + clear dry-run paper fills/positions — only when starting live.

    Set ``closed_only=true`` to drop completed round-trip fill legs while
    keeping open positions (and unpaired open entry fills). Prefer
    ``scripts/purge_closed_trades.py --confirm`` on Windows native.

    During the dry-run review week prefer closed-only if clearing bad history;
    full wipe is for starting live. See docs/options-portal-paper-retention.md.
    """
    retention = paper_retention_config()
    if not retention.get("allow_purge_before_live", True):
        raise HTTPException(
            status_code=403,
            detail=(
                "trading.equity.paper_retention.allow_purge_before_live is false — "
                "enable it in config before purging"
            ),
        )
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail="confirm=true required to purge paper ledger",
        )

    from options_portal_persistence.paper_trades import PaperTradeStore

    store = PaperTradeStore()
    if not store.available:
        raise HTTPException(
            status_code=503,
            detail="Paper ledger unavailable (set PERSISTENCE_PROFILE=local)",
        )

    result = store.archive_and_purge(
        mode=body.mode,  # type: ignore[arg-type]
        confirm=True,
        archive=body.archive,
        clear_positions=False if body.closed_only else body.clear_positions,
        closed_only=body.closed_only,
    )
    if body.closed_only:
        message = (
            "Closed round-trip fills purged; open positions kept. "
            "Old bad exits (e.g. shared ₹996.96) are gone from Trades history."
        )
    else:
        message = (
            "Paper ledger purged. Review archive table / SQLite backup if needed, "
            "then proceed toward live only after Project unlock."
        )
    return {
        **result,
        "trading_mode": trading_mode(),
        "retention": retention,
        "message": message,
    }
