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

# Equity dry-run paper ledger (share buy/sell fills + open positions).
PAPER_LEDGER_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS paper_positions (
  mode          TEXT NOT NULL,
  symbol        TEXT NOT NULL,
  side          TEXT NOT NULL,  -- long | short
  qty           INTEGER NOT NULL,
  avg_price     REAL NOT NULL,
  opened_at     TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  PRIMARY KEY (mode, symbol)
);

CREATE TABLE IF NOT EXISTS paper_fills (
  id                TEXT PRIMARY KEY,
  client_order_id   TEXT NOT NULL UNIQUE,
  broker_order_id   TEXT,
  mode              TEXT NOT NULL,
  symbol            TEXT NOT NULL,
  side              TEXT NOT NULL,  -- BUY | SELL
  qty               INTEGER NOT NULL,
  price             REAL NOT NULL,
  status            TEXT NOT NULL,
  reason            TEXT NOT NULL DEFAULT '',
  signal_action     TEXT,
  action_kind       TEXT,
  position_side     TEXT,           -- long | short | null after exit
  dry_run           INTEGER NOT NULL DEFAULT 1,
  realized_pnl      REAL,
  ts_utc            TEXT NOT NULL,
  ts_ist            TEXT NOT NULL,
  ts_ist_display    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS paper_fills_ts_idx
  ON paper_fills (ts_utc DESC);

CREATE INDEX IF NOT EXISTS paper_fills_symbol_idx
  ON paper_fills (symbol, ts_utc DESC);
"""


def apply_news_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(NEWS_SCHEMA_SQL)
    conn.commit()


def apply_paper_ledger_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(PAPER_LEDGER_SCHEMA_SQL)
    conn.commit()
