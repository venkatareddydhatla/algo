"""Connection helpers — no credentials hardcoded.

Supports a lightweight ``local`` / ``sqlite`` persistence profile for native
dry-run without Postgres or Redis (Docker-free Windows/Linux first boot).
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from pathlib import Path
from typing import Any


def persistence_profile() -> str:
    """Return ``local``, ``sqlite``, or ``postgres`` (default).

    ``PERSISTENCE_PROFILE=local|sqlite`` forces the file/memory path.
    A ``DATABASE_URL`` that starts with ``sqlite`` also selects local mode.
    """
    explicit = (os.environ.get("PERSISTENCE_PROFILE") or "").strip().lower()
    if explicit in {"local", "sqlite", "postgres"}:
        return "local" if explicit == "sqlite" else explicit
    url = (os.environ.get("DATABASE_URL") or "").strip().lower()
    if url.startswith("sqlite"):
        return "local"
    return "postgres"


def is_local_profile() -> bool:
    return persistence_profile() == "local"


def database_url() -> str:
    if is_local_profile():
        return os.environ.get(
            "DATABASE_URL",
            "sqlite:///./data/options_portal.local.sqlite",
        )
    return os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://portal:change-me-strong@127.0.0.1:5432/options_portal",
    )


def redis_url() -> str:
    if is_local_profile():
        return os.environ.get("REDIS_URL", "memory://local")
    return os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")


def _sqlite_path_from_url(url: str) -> Path:
    """Map sqlite URL forms to a filesystem path (relative to CWD / absolute)."""
    raw = url.strip()
    for prefix in (
        "sqlite+aiosqlite:///",
        "sqlite:///",
        "sqlite://",
    ):
        if raw.lower().startswith(prefix):
            path_part = raw[len(prefix) :]
            break
    else:
        path_part = raw
    if path_part in {":memory:", ""} or path_part.startswith(":memory:"):
        return Path(":memory:")
    return Path(path_part)


def _ping_sqlite_sync(url: str) -> dict[str, Any]:
    path = _sqlite_path_from_url(url)
    try:
        if path.name == ":memory:" or str(path) == ":memory:":
            conn = sqlite3.connect(":memory:")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(path))
        try:
            val = conn.execute("SELECT 1").fetchone()
            return {
                "component": "sqlite",
                "status": "ok",
                "result": val[0] if val else None,
                "path": str(path),
                "profile": "local",
            }
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return {
            "component": "sqlite",
            "status": "down",
            "detail": str(exc),
            "profile": "local",
        }


async def check_postgres() -> dict[str, Any]:
    """Best-effort DB ping. Local profile uses SQLite; else Postgres.

    Returns a status dict (does not raise).
    """
    if is_local_profile():
        return await asyncio.to_thread(_ping_sqlite_sync, database_url())

    url = database_url()
    try:
        import asyncpg

        # asyncpg wants postgresql:// not postgresql+asyncpg://
        dsn = url.replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(dsn, timeout=3)
        try:
            val = await conn.fetchval("SELECT 1")
            return {"component": "postgres", "status": "ok", "result": val}
        finally:
            await conn.close()
    except Exception as exc:  # noqa: BLE001
        return {"component": "postgres", "status": "down", "detail": str(exc)}


async def check_redis() -> dict[str, Any]:
    """Best-effort Redis ping. Local profile returns an in-process stub OK."""
    if is_local_profile():
        return {
            "component": "redis",
            "status": "ok",
            "detail": "local stub (no Redis required for dry_run)",
            "profile": "local",
        }

    try:
        from redis.asyncio import Redis

        client = Redis.from_url(redis_url(), socket_connect_timeout=2)
        try:
            pong = await client.ping()
            return {"component": "redis", "status": "ok" if pong else "degraded"}
        finally:
            await client.aclose()
    except Exception as exc:  # noqa: BLE001
        return {"component": "redis", "status": "down", "detail": str(exc)}
