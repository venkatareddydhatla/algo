"""RSS → VADER → SQLite news ingest cycle (shared by worker + API poller)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from options_portal_domain.config import load_config
from options_portal_domain.universe import (
    default_universe_csv,
    load_universe_csv,
    resolve_universe_path,
)
from options_portal_news.rss_poller import MultiSourceNewsPoller
from options_portal_news.symbol_tags import build_symbol_keywords
from options_portal_persistence.news_store import NewsStore
from options_portal_sentiment.vader_scorer import VaderScorer

logger = logging.getLogger(__name__)


def _symbol_keywords(cfg: dict[str, Any]) -> dict[str, list[str]]:
    trading = cfg.get("trading", {})
    path = None
    symbols_file = trading.get("symbols_file")
    universe = trading.get("universe") or trading.get("symbols")
    if symbols_file:
        path = resolve_universe_path(str(symbols_file))
    elif isinstance(universe, str):
        path = resolve_universe_path(universe)
    if path is None or not getattr(path, "exists", lambda: False)() or not path.exists():
        path = default_universe_csv()
    try:
        underlyings = load_universe_csv(path)
    except FileNotFoundError:
        underlyings = []
    return build_symbol_keywords(underlyings)


async def run_news_ingest_once(
    config: dict[str, Any] | None = None,
    *,
    store: NewsStore | None = None,
) -> dict[str, Any]:
    """Fetch configured RSS sources, score with VADER, persist. Never raises."""
    cfg = config or load_config()
    news_cfg = cfg.get("news", {})
    sources = list(news_cfg.get("sources") or [])
    lookback = int(
        news_cfg.get("ingest_lookback_minutes")
        or max(int(cfg.get("hybrid", {}).get("news_lookback_minutes", 180)), 360)
    )
    timeout_sec = float(news_cfg.get("timeout_sec", 15))

    result: dict[str, Any] = {
        "fetched": 0,
        "inserted": 0,
        "sources": [s.get("id") for s in sources if s.get("enabled", True) is not False],
        "enabled_urls": [],
        "errors": [],
    }

    try:
        keywords = _symbol_keywords(cfg)
        poller = MultiSourceNewsPoller(
            sources,
            symbol_keywords=keywords,
            timeout_sec=timeout_sec,
        )
        result["enabled_urls"] = [ing.source for ing in poller.ingestors]
        since = datetime.now(tz=timezone.utc) - timedelta(minutes=lookback)
        items = await poller.fetch_all_since(since)
        result["fetched"] = len(items)
        if not items:
            return result

        scorer = VaderScorer()
        sentiments = await scorer.score_batch(items)
        news_store = store or NewsStore()
        if not news_store.available:
            result["errors"].append("news store unavailable (non-SQLite profile?)")
            return result
        inserted = news_store.upsert_scored(items, sentiments)
        result["inserted"] = inserted
        logger.info(
            "news ingest fetched=%d inserted=%d sources=%s",
            len(items),
            inserted,
            ",".join(result["enabled_urls"]),
        )
    except Exception as exc:  # noqa: BLE001 — never crash caller on news
        logger.exception("news ingest failed: %s", exc)
        result["errors"].append(str(exc))
    return result


async def news_poll_loop(
    stop_event: Any,
    *,
    interval_sec: int | None = None,
) -> None:
    """Background loop: poll RSS on news.poll_interval_sec until stop_event is set."""
    cfg = load_config()
    news_cfg = cfg.get("news", {})
    interval = int(
        interval_sec
        if interval_sec is not None
        else news_cfg.get("poll_interval_sec", 120)
    )
    store = NewsStore()
    logger.info(
        "News poller starting interval=%ss store=%s",
        interval,
        "ok" if store.available else "unavailable",
    )
    await run_news_ingest_once(cfg, store=store)
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
            break
        except TimeoutError:
            cfg = load_config()
            await run_news_ingest_once(cfg, store=store)
    logger.info("News poller stopped")
