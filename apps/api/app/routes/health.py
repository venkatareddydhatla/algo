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
    data_source = broker.get("market_data_source") or broker.get("data_source") or "synthetic"
    return {
        "status": status,
        "components": components,
        "degraded": degraded,
        "local_profile": is_local_profile(),
        "market_data_source": data_source,
        "data_source": data_source,
    }


@router.get("/api/v1/health")
async def health_dashboard() -> dict:
    """UI health strip payload."""
    payload = await ready()
    db_name = "sqlite" if is_local_profile() else "postgres"
    broker = payload["components"]["broker"]
    data_source = broker.get("market_data_source") or broker.get("data_source") or "synthetic"
    return {
        "status": payload["status"],
        "mode": payload["components"]["trading_mode"],
        "halt": payload["components"]["halt"],
        "persistence_profile": payload["components"].get("persistence_profile"),
        "market_data_source": data_source,
        "data_source": data_source,
        "components": [
            {"name": db_name, **payload["components"]["postgres"]},
            {"name": "redis", **payload["components"]["redis"]},
            {
                "name": "upstox",
                **broker,
                "detail": (
                    f"market_data={data_source}; {broker.get('detail', '')}"
                    if "market_data=" not in str(broker.get("detail", ""))
                    else broker.get("detail", "")
                ),
            },
            {"name": "worker", "status": "unknown", "detail": "heartbeat TODO"},
            {
                "name": "market_data",
                "status": "ok" if data_source == "upstox" else "mock",
                "detail": f"source={data_source}",
                "data_source": data_source,
            },
        ],
    }
