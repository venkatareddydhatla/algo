"""Best-effort per-symbol price marks (live LTP → candle close → last-known)."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

logger = logging.getLogger(__name__)

# Process-level cache of last successful live LTP per symbol.
# Used so a transient Upstox tick/batch failure does not zero open uPnL.
_LAST_KNOWN_LTP: dict[str, float] = {}


def clear_last_known_ltp_cache() -> None:
    """Test helper — wipe retained LTP marks."""
    _LAST_KNOWN_LTP.clear()


def remember_ltp_marks(marks: dict[str, float]) -> None:
    """Retain positive per-symbol LTP marks for later tick gaps."""
    for raw, px in marks.items():
        sym = str(raw or "").strip().upper()
        if not sym:
            continue
        try:
            price = float(px)
        except (TypeError, ValueError):
            continue
        if price > 0:
            _LAST_KNOWN_LTP[sym] = price


def last_known_ltp_map(symbols: Sequence[str]) -> dict[str, float]:
    """Return previously retained live LTP marks for ``symbols`` (no synthetic)."""
    out: dict[str, float] = {}
    for raw in symbols:
        sym = str(raw or "").strip().upper()
        if not sym or sym in out:
            continue
        px = _LAST_KNOWN_LTP.get(sym)
        if px is not None and px > 0:
            out[sym] = px
    return out


async def live_ltp_map(symbols: Sequence[str]) -> dict[str, float]:
    """Best-effort Upstox LTP map keyed by uppercased symbol (never raises).

    Successful prices are retained in ``_LAST_KNOWN_LTP``. A broker ``aclose``
    failure after a successful fetch must not discard the map (that previously
    zeroed the whole Trades uPnL table).
    """
    uniq = sorted({str(s).upper() for s in symbols if s})
    if not uniq:
        return {}
    try:
        from options_portal_broker.adapter import UpstoxAdapter

        broker = UpstoxAdapter()
        marks: dict[str, float] = {}
        try:
            marks = await broker.get_ltp_map(uniq)
        finally:
            try:
                await broker.aclose()
            except Exception:  # noqa: BLE001
                logger.warning("broker aclose failed after LTP map", exc_info=True)
        remember_ltp_marks(marks)
        return marks
    except Exception:  # noqa: BLE001
        logger.exception("live LTP map unavailable")
        return {}


async def open_position_marks_for_symbols(
    symbols: Sequence[str],
) -> tuple[dict[str, float], dict[str, str]]:
    """Resolve open-position uPnL marks: live LTP, else last-known live LTP.

    Partial quote failures keep successful symbols and fill gaps from the
    process cache — never wipe the whole table because one tick failed.
    Synthetic display prices are intentionally not used for uPnL.
    """
    uniq = sorted({str(s).upper() for s in symbols if s})
    prices: dict[str, float] = {}
    sources: dict[str, str] = {}
    if not uniq:
        return prices, sources

    live = await live_ltp_map(uniq)
    for sym, px in live.items():
        prices[sym] = px
        sources[sym] = "ltp"

    missing = [s for s in uniq if s not in prices]
    if missing:
        cached = last_known_ltp_map(missing)
        for sym, px in cached.items():
            prices[sym] = px
            sources[sym] = "last_known"
            logger.warning(
                "trades uPnL: live tick gap for %s — keeping last-known mark=%.4f",
                sym,
                px,
            )
        still = sorted(s for s in missing if s not in cached)
        for sym in still:
            logger.warning(
                "trades uPnL: no LTP or last-known for %s — leaving uPnL 0",
                sym,
            )
        if still:
            logger.warning(
                "trades uPnL mark gaps (%d/%d): %s",
                len(still),
                len(uniq),
                ",".join(still),
            )
    return prices, sources


async def _live_15m_candles_map(
    symbols: Sequence[str],
) -> dict[str, list[Any]]:
    """Fetch Upstox 15m candles per symbol (empty map without access token)."""
    uniq = sorted({str(s).upper() for s in symbols if s})
    if not uniq:
        return {}
    if not (os.environ.get("UPSTOX_ACCESS_TOKEN") or "").strip():
        return {}
    out: dict[str, list[Any]] = {}
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
                    ):
                        out[sym] = list(candles)
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "15m candles fetch failed for %s", sym, exc_info=True
                    )
        finally:
            await broker.aclose()
    except Exception:  # noqa: BLE001
        logger.exception("15m candles map unavailable")
    return out


async def candle_close_map(symbols: Sequence[str]) -> dict[str, float]:
    """Last candle close per symbol when live candles are available.

    Skips the synthetic path when there is no access token (caller should use
    last-known/synthetic display marks instead of pretending these are live).
    """
    closes, _hl = await candle_close_and_session_hl_map(symbols)
    return closes


async def session_hl_map(
    symbols: Sequence[str],
) -> dict[str, tuple[float, float]]:
    """Today's session (high, low) from live 15m bars per symbol."""
    _closes, hl = await candle_close_and_session_hl_map(symbols)
    return hl


async def candle_close_and_session_hl_map(
    symbols: Sequence[str],
) -> tuple[dict[str, float], dict[str, tuple[float, float]]]:
    """One 15m fetch → last close + today's session (high, low) per symbol."""
    from options_portal_domain.equity import session_mark_bounds

    closes: dict[str, float] = {}
    hl: dict[str, tuple[float, float]] = {}
    candles_by_sym = await _live_15m_candles_map(symbols)
    for sym, candles in candles_by_sym.items():
        if not candles:
            continue
        try:
            px = float(candles[-1].close)
        except (TypeError, ValueError, AttributeError):
            px = 0.0
        if px > 0:
            closes[sym] = px
        bounds = session_mark_bounds(candles)
        if bounds is not None:
            hl[sym] = bounds
    return closes, hl


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
