"""Thin re-export — ingest lives in options_portal_news.ingest."""

from options_portal_news.ingest import news_poll_loop, run_news_ingest_once

__all__ = ["news_poll_loop", "run_news_ingest_once"]
