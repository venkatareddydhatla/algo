"""Optional in-process RSS poller for local API (Windows native without worker)."""

from __future__ import annotations

import asyncio
import logging

from options_portal_news.ingest import news_poll_loop

logger = logging.getLogger(__name__)


async def run_api_news_poller(stop: asyncio.Event) -> None:
    logger.info("Starting embedded RSS news poller")
    await news_poll_loop(stop)
