"""Admin config placeholders — live enablement stays gated."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.mock_data import MOCK_CONFIG
from options_portal_domain.config import load_config

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class ModeUpdate(BaseModel):
    mode: str = Field(pattern="^(dry_run|demo|live)$")
    confirm_live: bool = False


@router.get("/config")
async def get_config() -> dict:
    cfg = load_config()
    return {
        "trading": cfg.get("trading", MOCK_CONFIG["trading"]),
        "hybrid": cfg.get("hybrid", MOCK_CONFIG["hybrid"]),
        "source": "config+defaults",
        "v1_note": "Live order placement remains blocked regardless of mode switch",
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
    return {"halt": halt, "persisted": False, "message": "Kill switch stub — set TRADING_HALT env for process-level halt"}
