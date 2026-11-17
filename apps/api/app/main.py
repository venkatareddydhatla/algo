"""FastAPI entrypoint for the Options Portal API."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes import admin, backtest, health, market, news, signals, trades, watchlist
from options_portal_domain.config import load_config, trading_mode
from options_portal_domain.tz import ensure_tzdata
from options_portal_persistence.db import is_local_profile

# Windows ships without system IANA zones — load tzdata before any ZoneInfo use.
ensure_tzdata()

logger = logging.getLogger("api")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    cfg = load_config()
    mode = trading_mode(cfg)
    if mode == "live":
        # Soft warning only — router still blocks live
        print(
            "WARNING: TRADING_MODE=live requested but v1 blocks live placement. "
            "Prefer dry_run."
        )

    news_task: asyncio.Task | None = None
    stop = asyncio.Event()
    # Local/SQLite: poll RSS inside the API so News UI works without -WithWorker.
    # Worker also runs the same loop; UNIQUE(source, content_hash) keeps upserts safe.
    poll_in_api = os.environ.get("NEWS_POLL_IN_API", "1").strip().lower() not in {
        "0",
        "false",
        "no",
    }
    if poll_in_api and is_local_profile():
        from app.news_poller import run_api_news_poller

        news_task = asyncio.create_task(run_api_news_poller(stop), name="api-news-poll")
        logger.info("API news poller started (local profile)")

    yield

    stop.set()
    if news_task is not None:
        try:
            await asyncio.wait_for(news_task, timeout=8)
        except (TimeoutError, asyncio.CancelledError):
            news_task.cancel()


app = FastAPI(
    title="Options Portal API",
    version="0.1.0",
    description="NSE/BSE F&O options signal portal — dry-run default",
    lifespan=lifespan,
)

origins = [
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:80",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(market.router)
app.include_router(signals.router)
app.include_router(watchlist.router)
app.include_router(news.router)
app.include_router(trades.router)
app.include_router(backtest.router)
app.include_router(admin.router)


@app.get("/")
async def root() -> dict:
    return {
        "app": "options-portal",
        "mode": trading_mode(),
        "docs": "/docs",
        "health": "/health/ready",
    }


def run() -> None:
    import uvicorn

    host = os.environ.get("BIND_HOST", "127.0.0.1")
    port = int(os.environ.get("API_PORT", "8000"))
    uvicorn.run("app.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    run()
