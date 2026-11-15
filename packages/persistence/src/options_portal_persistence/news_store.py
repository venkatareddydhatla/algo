"""News + sentiment persistence (SQLite local profile).

Postgres full schema is reserved for Compose; Windows native uses SQLite.

Retention (IST):
- Live ``news_items`` = portal feed (today); older rows archive daily into history.
- ``news_items_history`` kept until calendar quarter-end, then purged.
- Portal ``/news`` reads live only — history is for research / future admin API.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

from options_portal_domain.tz import current_quarter_start, ist, ist_day_start, now_ist
from options_portal_domain.types import NewsItem, SentimentResult
from options_portal_persistence.db import database_url, is_local_profile, _sqlite_path_from_url
from options_portal_persistence.sqlite_schema import apply_news_schema

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


class NewsStore:
    """SQLite-backed news repository used by worker ingest and API reads."""

    def __init__(self, db_url: str | None = None) -> None:
        self._url = db_url or database_url()
        self._path: Path | None = None
        if is_local_profile() or self._url.lower().startswith("sqlite"):
            path = _sqlite_path_from_url(self._url)
            if str(path) != ":memory:" and path.name != ":memory:":
                path.parent.mkdir(parents=True, exist_ok=True)
            self._path = path
            self._ensure_schema()
        else:
            logger.warning(
                "NewsStore: non-SQLite profile — live news read/write disabled "
                "(use PERSISTENCE_PROFILE=local for native dry-run)"
            )

    @property
    def available(self) -> bool:
        return self._path is not None

    def _connect(self) -> sqlite3.Connection:
        assert self._path is not None
        if str(self._path) == ":memory:" or self._path.name == ":memory:":
            # Memory DB would be empty each connect — use a shared named memory URI
            conn = sqlite3.connect(
                "file:options_portal_news?mode=memory&cache=shared",
                uri=True,
                check_same_thread=False,
            )
        else:
            conn = sqlite3.connect(str(self._path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            apply_news_schema(conn)

    def upsert_scored(
        self,
        items: Sequence[NewsItem],
        sentiments: Sequence[SentimentResult],
    ) -> int:
        """Insert or ignore news rows; upsert sentiment. Returns rows newly inserted."""
        if not self.available or not items:
            return 0
        by_id = {s.news_id: s for s in sentiments}
        inserted = 0
        now = _utc_now_iso()
        with self._connect() as conn:
            apply_news_schema(conn)
            for item in items:
                content_hash = item.id if len(item.id) >= 32 else item.id
                # Prefer full hash when body stored the truncated id from poller
                pub = item.published_at
                if pub.tzinfo is None:
                    pub = pub.replace(tzinfo=timezone.utc)
                pub_iso = pub.astimezone(timezone.utc).isoformat()
                symbols_json = json.dumps(list(item.symbols or []), separators=(",", ":"))
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO news_items
                      (id, source, url, title, body, published_at, ingested_at,
                       content_hash, symbols_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.id,
                        item.source,
                        item.url,
                        item.title,
                        item.body,
                        pub_iso,
                        now,
                        content_hash,
                        symbols_json,
                    ),
                )
                if cur.rowcount:
                    inserted += 1
                sent = by_id.get(item.id)
                if sent is None:
                    continue
                conn.execute(
                    """
                    INSERT INTO news_sentiment
                      (news_id, model, score, label, confidence, scored_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(news_id) DO UPDATE SET
                      model=excluded.model,
                      score=excluded.score,
                      label=excluded.label,
                      confidence=excluded.confidence,
                      scored_at=excluded.scored_at
                    """,
                    (
                        item.id,
                        sent.model,
                        float(sent.score),
                        sent.label,
                        float(sent.confidence),
                        now,
                    ),
                )
            conn.commit()
        return inserted

    def count(self) -> int:
        if not self.available:
            return 0
        with self._connect() as conn:
            apply_news_schema(conn)
            row = conn.execute("SELECT COUNT(*) AS c FROM news_items").fetchone()
            return int(row["c"] if row else 0)

    def list_recent(
        self,
        *,
        symbol: str | None = None,
        lookback_minutes: int | None = None,
        limit: int = 100,
        live_today_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Return API-shaped news rows from the **live** table (newest first).

        History is never included. When ``live_today_only`` is True, only rows
        with ``published_at`` on/after today's IST midnight are returned.
        """
        if not self.available:
            return []
        cutoff: datetime | None = None
        if lookback_minutes is not None and lookback_minutes > 0:
            cutoff = datetime.now(tz=timezone.utc) - timedelta(minutes=lookback_minutes)
        if live_today_only:
            day_start = ist_day_start().astimezone(timezone.utc)
            cutoff = day_start if cutoff is None else max(cutoff, day_start)
        with self._connect() as conn:
            apply_news_schema(conn)
            rows = conn.execute(
                """
                SELECT n.id, n.source, n.url, n.title, n.body, n.published_at,
                       n.symbols_json,
                       s.score, s.label, s.model, s.confidence
                FROM news_items n
                LEFT JOIN news_sentiment s ON s.news_id = n.id
                ORDER BY n.published_at DESC
                LIMIT ?
                """,
                (max(limit * 4, 200),),  # over-fetch then filter by symbol/cutoff
            ).fetchall()

        out: list[dict[str, Any]] = []
        needle = symbol.upper() if symbol else None
        for row in rows:
            pub = _parse_iso(row["published_at"])
            if cutoff and pub and pub < cutoff:
                continue
            try:
                symbols = json.loads(row["symbols_json"] or "[]")
            except json.JSONDecodeError:
                symbols = []
            if not isinstance(symbols, list):
                symbols = []
            symbols = [str(s).upper() for s in symbols]
            if needle and needle not in symbols:
                continue
            score = row["score"]
            label = row["label"] or "neutral"
            if score is None:
                score = 0.0
                label = "neutral"
            out.append(
                {
                    "id": row["id"],
                    "published_at": row["published_at"],
                    "score": float(score),
                    "label": label,
                    "symbols": symbols,
                    "title": row["title"],
                    "source": row["source"],
                    "url": row["url"],
                    "body": row["body"] or "",
                    "model": row["model"] or "vader",
                    "confidence": float(row["confidence"] or 0.0),
                }
            )
            if len(out) >= limit:
                break
        return out

    def count_history(self) -> int:
        if not self.available:
            return 0
        with self._connect() as conn:
            apply_news_schema(conn)
            row = conn.execute("SELECT COUNT(*) AS c FROM news_items_history").fetchone()
            return int(row["c"] if row else 0)

    def run_retention(
        self,
        *,
        as_of: datetime | None = None,
    ) -> dict[str, int]:
        """Daily live→history archive + purge history before current quarter.

        - Move live rows with ``published_at`` before today's IST midnight into history.
        - Delete history rows archived before the current IST calendar quarter start.
        """
        if not self.available:
            return {"archived": 0, "purged": 0}
        when = as_of if as_of is not None else now_ist()
        if when.tzinfo is None:
            when = when.replace(tzinfo=ist())
        else:
            when = when.astimezone(ist())
        day_start_utc = ist_day_start(when.date()).astimezone(timezone.utc).isoformat()
        q_start = current_quarter_start(when.date())
        q_start_utc = ist_day_start(q_start).astimezone(timezone.utc).isoformat()
        archived_at = _utc_now_iso()

        with self._connect() as conn:
            apply_news_schema(conn)
            stale = conn.execute(
                """
                SELECT n.id, n.source, n.url, n.title, n.body, n.published_at,
                       n.ingested_at, n.content_hash, n.symbols_json,
                       s.model, s.score, s.label, s.confidence, s.scored_at
                FROM news_items n
                LEFT JOIN news_sentiment s ON s.news_id = n.id
                WHERE n.published_at < ?
                """,
                (day_start_utc,),
            ).fetchall()

            archived = 0
            for row in stale:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO news_items_history
                      (id, source, url, title, body, published_at, ingested_at,
                       content_hash, symbols_json, archived_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["id"],
                        row["source"],
                        row["url"],
                        row["title"],
                        row["body"],
                        row["published_at"],
                        row["ingested_at"],
                        row["content_hash"],
                        row["symbols_json"],
                        archived_at,
                    ),
                )
                if row["model"] is not None:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO news_sentiment_history
                          (news_id, model, score, label, confidence, scored_at, archived_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            row["id"],
                            row["model"],
                            float(row["score"]),
                            row["label"],
                            float(row["confidence"]),
                            row["scored_at"],
                            archived_at,
                        ),
                    )
                conn.execute("DELETE FROM news_sentiment WHERE news_id = ?", (row["id"],))
                conn.execute("DELETE FROM news_items WHERE id = ?", (row["id"],))
                archived += 1

            # Purge history from prior quarters (keep through current quarter-end).
            purge_ids = [
                r["id"]
                for r in conn.execute(
                    """
                    SELECT id FROM news_items_history
                    WHERE archived_at < ?
                    """,
                    (q_start_utc,),
                ).fetchall()
            ]
            for nid in purge_ids:
                conn.execute("DELETE FROM news_sentiment_history WHERE news_id = ?", (nid,))
                conn.execute("DELETE FROM news_items_history WHERE id = ?", (nid,))
            purged = len(purge_ids)
            conn.commit()

        if archived or purged:
            logger.info(
                "news retention archived=%d purged=%d as_of_ist=%s",
                archived,
                purged,
                when.isoformat(),
            )
        return {"archived": archived, "purged": purged}

    def recent_for_symbol(
        self,
        symbol: str,
        *,
        lookback_minutes: int = 180,
        limit: int = 20,
    ) -> tuple[list[NewsItem], list[SentimentResult]]:
        """Domain objects for hybrid pipeline."""
        rows = self.list_recent(
            symbol=symbol,
            lookback_minutes=lookback_minutes,
            limit=limit,
        )
        items: list[NewsItem] = []
        sentiments: list[SentimentResult] = []
        for row in rows:
            pub = _parse_iso(row["published_at"]) or datetime.now(tz=timezone.utc)
            items.append(
                NewsItem(
                    id=row["id"],
                    source=row["source"],
                    title=row["title"],
                    body=row.get("body") or "",
                    published_at=pub,
                    symbols=list(row.get("symbols") or []),
                    url=row.get("url"),
                )
            )
            sentiments.append(
                SentimentResult(
                    news_id=row["id"],
                    model=row.get("model") or "vader",
                    score=float(row["score"]),
                    label=row.get("label") or "neutral",  # type: ignore[arg-type]
                    confidence=float(row.get("confidence") or 0.0),
                )
            )
        return items, sentiments

    def recent_by_symbol(
        self,
        *,
        lookback_minutes: int = 180,
        limit_per_symbol: int = 10,
    ) -> dict[str, list[dict[str, Any]]]:
        """Map symbol → recent scored news rows (for dry-run reason overlay)."""
        rows = self.list_recent(lookback_minutes=lookback_minutes, limit=500)
        by_sym: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            for sym in row.get("symbols") or []:
                key = str(sym).upper()
                bucket = by_sym.setdefault(key, [])
                if len(bucket) < limit_per_symbol:
                    bucket.append(row)
        return by_sym
