"""Liveness / readiness health endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from options_portal_broker.adapter import UpstoxAdapter
from options_portal_domain.config import load_config, trading_mode
from options_portal_persistence.db import (
    check_postgres,
    check_redis,
    is_local_profile,
    persistence_profile,
)

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def live() -> dict:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready() -> dict:
    cfg = load_config()
    pg = await check_postgres()
    rd = await check_redis()
    broker = await UpstoxAdapter().health()
    profile = persistence_profile()
    # Keep postgres/redis keys for UI compatibility; local profile uses SQLite + stub.
    components = {
        "postgres": pg,
        "redis": rd,
        "broker": broker,
        "trading_mode": trading_mode(cfg),
        "halt": bool(cfg.get("trading", {}).get("halt", False)),
        "persistence_profile": profile,
    }
    # Ready if process is up; degraded deps surface in payload (local-friendly)
    degraded = [
        name
        for name, c in (("postgres", pg), ("redis", rd))
        if c.get("status") == "down"
    ]
    status = "ok" if not degraded else "degraded"
    return {
        "status": status,
        "components": components,
        "degraded": degraded,
        "local_profile": is_local_profile(),
    }


@router.get("/api/v1/health")
async def health_dashboard() -> dict:
    """UI health strip payload."""
    payload = await ready()
    db_name = "sqlite" if is_local_profile() else "postgres"
    return {
        "status": payload["status"],
        "mode": payload["components"]["trading_mode"],
        "halt": payload["components"]["halt"],
        "persistence_profile": payload["components"].get("persistence_profile"),
        "components": [
            {"name": db_name, **payload["components"]["postgres"]},
            {"name": "redis", **payload["components"]["redis"]},
            {"name": "upstox", **payload["components"]["broker"]},
            {"name": "worker", "status": "unknown", "detail": "heartbeat TODO"},
        ],
    }
