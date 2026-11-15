# News retention (live vs history)

Policy for the Options Portal **News** screen and SQLite local profile (`PERSISTENCE_PROFILE=local`).

## Summary

| Store | What | Portal News UI | Lifetime |
|-------|------|----------------|----------|
| **Live** (`news_items`) | Today's feed (IST calendar day) | **Shown** | Cleared daily → moved to history |
| **History** (`news_items_history`) | Prior live rows (expiry / research) | **Not shown** | Kept until **calendar quarter-end** (IST), then purged |

Sentiment mirrors the same split: `news_sentiment` (live) / `news_sentiment_history` (archived).

## Daily clear

On each news poll cycle (worker and/or embedded API poller) and on `/api/v1/news` reads:

1. Rows in `news_items` whose `published_at` is **before today's IST midnight** are copied into `news_items_history` (with `archived_at`) and removed from live.
2. Matching sentiment rows move to `news_sentiment_history`.

The portal News page and `GET /api/v1/news` return **live today-only** rows. History is never included in that response (optional admin/internal history API can be added later).

## Quarter-end history

History is retained for **expiry research** within the current IST calendar quarter (Q1=Jan–Mar, Q2=Apr–Jun, Q3=Jul–Sep, Q4=Oct–Dec).

After a new quarter starts, rows whose `archived_at` falls **before** the current quarter start are purged from history tables.

## Config

```yaml
news:
  retention:
    live: today_ist
    history: until_quarter_end
```

RSS ingest may still fetch a multi-day lookback (`ingest_lookback_minutes`); items from prior IST days are archived off the live portal feed on the next retention pass.

## Windows native

Uses the same SQLite file under `data\`. No extra services — retention runs inside the API news poller and/or worker. See [options-portal-windows-native.md](./options-portal-windows-native.md).
