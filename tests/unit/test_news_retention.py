"""Daily live→history archive and quarter-end purge."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from options_portal_domain.types import NewsItem, SentimentResult
from options_portal_domain.tz import ist, ist_day_start
from options_portal_persistence.news_store import NewsStore


def _item(
    nid: str,
    *,
    published: datetime,
    title: str = "Headline",
) -> NewsItem:
    return NewsItem(
        id=nid,
        source="test",
        title=title,
        body="body",
        published_at=published,
        symbols=["NIFTY"],
        url="https://example.com/" + nid[:8],
    )


def _sent(nid: str) -> SentimentResult:
    return SentimentResult(
        news_id=nid,
        model="vader",
        score=0.2,
        label="bullish",
        confidence=0.5,
    )


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> NewsStore:
    db = tmp_path / "news.sqlite"
    monkeypatch.setenv("PERSISTENCE_PROFILE", "local")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    return NewsStore()


def test_archive_moves_prior_ist_day_out_of_live(store: NewsStore):
    today = datetime(2026, 11, 15, 12, 0, tzinfo=ist())
    day_start = ist_day_start(today.date())
    yesterday = day_start - timedelta(hours=3)
    this_morning = day_start + timedelta(hours=2)

    old_id = "a" * 32
    new_id = "b" * 32
    store.upsert_scored(
        [_item(old_id, published=yesterday.astimezone(timezone.utc), title="Old"),
         _item(new_id, published=this_morning.astimezone(timezone.utc), title="New")],
        [_sent(old_id), _sent(new_id)],
    )
    assert store.count() == 2

    result = store.run_retention(as_of=today)
    assert result["archived"] == 1
    assert store.count() == 1
    assert store.count_history() == 1

    live = store.list_recent(live_today_only=True, limit=10)
    assert len(live) == 1
    assert live[0]["id"] == new_id
    assert live[0]["title"] == "New"

    # History must not appear in live listing even without today filter
    all_live = store.list_recent(limit=50)
    assert {r["id"] for r in all_live} == {new_id}


def test_idempotent_retention_and_quarter_purge(store: NewsStore):
    # Mid-quarter day
    as_of = datetime(2026, 11, 15, 10, 0, tzinfo=ist())
    day_start = ist_day_start(as_of.date())
    prior = day_start - timedelta(days=1)

    nid = "c" * 32
    store.upsert_scored(
        [_item(nid, published=prior.astimezone(timezone.utc))],
        [_sent(nid)],
    )
    first = store.run_retention(as_of=as_of)
    assert first["archived"] == 1
    second = store.run_retention(as_of=as_of)
    assert second["archived"] == 0
    assert store.count_history() == 1

    # Next quarter: history from prior quarter should purge
    next_q = datetime(2027, 1, 2, 9, 0, tzinfo=ist())
    purged = store.run_retention(as_of=next_q)
    assert purged["purged"] == 1
    assert store.count_history() == 0


def test_list_recent_live_today_only_filters_without_archive(store: NewsStore):
    today = datetime(2026, 11, 15, 14, 0, tzinfo=ist())
    day_start = ist_day_start(today.date())
    old_id = "d" * 32
    new_id = "e" * 32
    store.upsert_scored(
        [
            _item(old_id, published=(day_start - timedelta(hours=1)).astimezone(timezone.utc)),
            _item(new_id, published=(day_start + timedelta(hours=1)).astimezone(timezone.utc)),
        ],
        [_sent(old_id), _sent(new_id)],
    )
    # Before archive, today filter still hides yesterday from portal-shaped reads
    live = store.list_recent(live_today_only=True, limit=10)
    assert [r["id"] for r in live] == [new_id]
