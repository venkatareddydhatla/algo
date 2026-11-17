"""Best-effort per-symbol price marks (live LTP → candle close → last-known)."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

logger = logging.getLogger(__name__)


async def live_ltp_map(symbols: Sequence[str]) -> dict[str, float]:
    """Best-effort Upstox LTP map keyed by uppercased symbol (never raises)."""
    uniq = sorted({str(s).upper() for s in symbols if s})
    if not uniq:
        return {}
    try:
        from options_portal_broker.adapter import UpstoxAdapter

        broker = UpstoxAdapter()
        try:
            return await broker.get_ltp_map(uniq)
        finally:
            await broker.aclose()
    except Exception:  # noqa: BLE001
        logger.exception("live LTP map unavailable")
        return {}


async def candle_close_map(symbols: Sequence[str]) -> dict[str, float]:
    """Last candle close per symbol when live candles are available.

    Skips the synthetic path when there is no access token (caller should use
    last-known/synthetic display marks instead of pretending these are live).
    """
    uniq = sorted({str(s).upper() for s in symbols if s})
    if not uniq:
        return {}
    if not (os.environ.get("UPSTOX_ACCESS_TOKEN") or "").strip():
        return {}
    out: dict[str, float] = {}
    try:
        from options_portal_broker.adapter import UpstoxAdapter

        broker = UpstoxAdapter()
        end = datetime.now(tz=timezone.utc)
        start = end - timedelta(days=3)
        try:
            for sym in uniq:
                try:
                    candles = await broker.get_candles(sym, "15m", start, end)
                    if (
                        candles
                        and getattr(broker, "last_candle_source", None) == "upstox"
                        and candles[-1].close
                    ):
                        px = float(candles[-1].close)
                        if px > 0:
                            out[sym] = px
                except Exception:  # noqa: BLE001
                    logger.warning("candle close mark failed for %s", sym, exc_info=True)
        finally:
            await broker.aclose()
    except Exception:  # noqa: BLE001
        logger.exception("candle close map unavailable")
    return out


def last_known_display_map(symbols: Sequence[str]) -> dict[str, float]:
    """Stable per-symbol display mark when live MD is unavailable."""
    from options_portal_broker.adapter import _synthetic_base_price

    out: dict[str, float] = {}
    for raw in symbols:
        sym = str(raw or "").strip().upper()
        if not sym or sym in out:
            continue
        px = float(_synthetic_base_price(sym))
        if px > 0:
            out[sym] = px
    return out


async def price_marks_for_symbols(
    symbols: Sequence[str],
) -> tuple[dict[str, float], dict[str, str]]:
    """Resolve display prices: LTP → live candle close → last-known synthetic.

    Returns ``(prices, sources)`` where sources are ``ltp``, ``candle_close``,
    or ``last_known``.
    """
    uniq = sorted({str(s).upper() for s in symbols if s})
    prices: dict[str, float] = {}
    sources: dict[str, str] = {}
    if not uniq:
        return prices, sources

    ltp = await live_ltp_map(uniq)
    for sym, px in ltp.items():
        prices[sym] = px
        sources[sym] = "ltp"

    missing = [s for s in uniq if s not in prices]
    if missing:
        closes = await candle_close_map(missing)
        for sym, px in closes.items():
            prices[sym] = px
            sources[sym] = "candle_close"

    still = [s for s in uniq if s not in prices]
    if still:
        for sym, px in last_known_display_map(still).items():
            prices[sym] = px
            sources[sym] = "last_known"

    return prices, sources


async def enrich_rows_with_prices(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Attach ``price`` / ``ltp`` / ``price_source`` onto each signal-like row."""
    symbols = [str(r.get("symbol") or "") for r in rows]
    prices, sources = await price_marks_for_symbols(symbols)
    for row in rows:
        sym = str(row.get("symbol") or "").upper()
        px = prices.get(sym)
        src = sources.get(sym)
        row["price"] = px
        row["price_source"] = src
        # ``ltp`` is only set when the mark is a live last trade
        row["ltp"] = px if src == "ltp" else None
    return {
        "marks_applied": sorted(s for s, src in sources.items() if src == "ltp"),
        "price_sources": {
            "ltp": sum(1 for s in sources.values() if s == "ltp"),
            "candle_close": sum(1 for s in sources.values() if s == "candle_close"),
            "last_known": sum(1 for s in sources.values() if s == "last_known"),
        },
    }
