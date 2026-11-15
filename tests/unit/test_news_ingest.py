"""RSS parse, VADER score, symbol tags, and SQLite persist path."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from options_portal_domain.types import NewsItem
from options_portal_domain.universe import Underlying
from options_portal_news.rss_poller import RssNewsIngestor
from options_portal_news.symbol_tags import build_symbol_keywords, tag_symbols
from options_portal_persistence.news_store import NewsStore
from options_portal_sentiment.vader_scorer import VaderScorer

SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Markets</title>
    <item>
      <title>Reliance shares surge on strong refining margins</title>
      <link>https://example.com/ril-1</link>
      <description>Broker upgrades RIL after rally in crude crack spreads.</description>
      <pubDate>Mon, 10 Nov 2025 10:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Quiet session for IT majors including TCS</title>
      <link>https://example.com/tcs-1</link>
      <description>Neutral tape ahead of results.</description>
      <pubDate>Mon, 10 Nov 2025 09:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""


def test_rss_parse_and_tag_symbols():
    ing = RssNewsIngestor(
        "test_feed",
        "https://example.com/feed.xml",
        symbol_keywords={
            "RELIANCE": ["reliance", "ril"],
            "TCS": ["tcs", "tata consultancy"],
        },
    )
    since = datetime(2025, 11, 1, tzinfo=timezone.utc)
    items = ing._parse(SAMPLE_RSS, since)
    assert len(items) == 2
    by_title = {i.title: i for i in items}
    assert "RELIANCE" in by_title["Reliance shares surge on strong refining margins"].symbols
    assert "TCS" in by_title["Quiet session for IT majors including TCS"].symbols


@pytest.mark.asyncio
async def test_placeholder_fetch_returns_empty():
    ing = RssNewsIngestor("nse", "REPLACE_WITH_OFFICIAL_NSE_NOTICES_FEED")
    items = await ing.fetch_since(datetime.now(tz=timezone.utc) - timedelta(hours=1))
    assert items == []


@pytest.mark.asyncio
async def test_vader_scores_batch():
    scorer = VaderScorer()
    items = [
        NewsItem(
            id="a" * 32,
            source="test",
            title="Markets rally on strong inflows and upgrade",
            body="Bullish surge gains",
            published_at=datetime.now(tz=timezone.utc),
            symbols=["NIFTY"],
        ),
        NewsItem(
            id="b" * 32,
            source="test",
            title="Fraud probe triggers sharp fall and downgrade",
            body="Bearish outflow weak cut",
            published_at=datetime.now(tz=timezone.utc),
            symbols=["RELIANCE"],
        ),
    ]
    results = await scorer.score_batch(items)
    assert len(results) == 2
    assert results[0].score > 0
    assert results[0].label == "bullish"
    assert results[1].score < 0
    assert results[1].label == "bearish"


def test_build_symbol_keywords_from_universe():
    rows = [
        Underlying(symbol="RELIANCE", name="RELIANCE INDUSTRIES LTD", kind="equity"),
        Underlying(symbol="NIFTY", name="NIFTY 50", kind="index"),
    ]
    mapping = build_symbol_keywords(rows)
    assert "RELIANCE" in mapping
    assert any("reliance" in k for k in mapping["RELIANCE"])
    hits = tag_symbols("Reliance Industries jumps in early trade", mapping)
    assert "RELIANCE" in hits


@pytest.mark.asyncio
async def test_news_store_persist_and_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "news.sqlite"
    monkeypatch.setenv("PERSISTENCE_PROFILE", "local")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")

    store = NewsStore()
    assert store.available
    assert store.count() == 0

    items = [
        NewsItem(
            id="c" * 32,
            source="moneycontrol",
            title="Bank Nifty rally lifts sentiment",
            body="Strong banking index gains",
            published_at=datetime.now(tz=timezone.utc),
            symbols=["BANKNIFTY", "NIFTY"],
            url="https://example.com/bn",
        )
    ]
    scorer = VaderScorer()
    sentiments = await scorer.score_batch(items)
    inserted = store.upsert_scored(items, sentiments)
    assert inserted == 1
    assert store.count() == 1

    # Idempotent
    assert store.upsert_scored(items, sentiments) == 0
    assert store.count() == 1

    rows = store.list_recent(limit=10)
    assert len(rows) == 1
    assert rows[0]["source"] == "moneycontrol"
    assert "BANKNIFTY" in rows[0]["symbols"]
    assert rows[0]["label"] in {"bullish", "bearish", "neutral"}

    by_sym = store.recent_by_symbol(lookback_minutes=180)
    assert "BANKNIFTY" in by_sym
    news, sents = store.recent_for_symbol("NIFTY", lookback_minutes=180)
    assert len(news) == 1
    assert len(sents) == 1
