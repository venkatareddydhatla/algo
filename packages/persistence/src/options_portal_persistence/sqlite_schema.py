"""SQLite DDL for local / native dry-run persistence."""

from __future__ import annotations

import sqlite3

# Simplified news tables for local profile (symbols stored as JSON — no instruments FK).
NEWS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS news_items (
  id            TEXT PRIMARY KEY,
  source        TEXT NOT NULL,
  url           TEXT,
  title         TEXT NOT NULL,
  body          TEXT,
  published_at  TEXT NOT NULL,
  ingested_at   TEXT NOT NULL,
  content_hash  TEXT NOT NULL,
  symbols_json  TEXT NOT NULL DEFAULT '[]',
  UNIQUE (source, content_hash)
);

CREATE INDEX IF NOT EXISTS news_items_published_idx
  ON news_items (published_at DESC);

CREATE TABLE IF NOT EXISTS news_sentiment (
  news_id       TEXT PRIMARY KEY REFERENCES news_items(id) ON DELETE CASCADE,
  model         TEXT NOT NULL,
  score         REAL NOT NULL,
  label         TEXT NOT NULL,
  confidence    REAL NOT NULL,
  scored_at     TEXT NOT NULL
);
"""


def apply_news_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(NEWS_SCHEMA_SQL)
    conn.commit()
