"""NIFTY / SENSEX index strip quotes + NSE cash market OPEN/CLOSE status."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from options_portal_domain.calendar import is_nse_trading_day
from options_portal_domain.config import load_config
from options_portal_domain.session_gates import SoftEodPolicy, is_cash_market_open
from options_portal_domain.tz import now_ist

logger = logging.getLogger(__name__)

INDEX_STRIP_SYMBOLS: tuple[str, ...] = ("NIFTY", "SENSEX")

# Process-level last successful snapshot per symbol (stale OK when market CLOSE).
_LAST_INDEX_QUOTES: dict[str, dict[str, Any]] = {}


def clear_index_quote_cache() -> None:
    """Test helper — wipe retained index snapshots."""
    _LAST_INDEX_QUOTES.clear()


def remember_index_quotes(quotes: dict[str, dict[str, Any]]) -> None:
    """Retain positive last_price snapshots for later CLOSE / tick-gap display."""
    for raw, snap in quotes.items():
        sym = str(raw or "").strip().upper()
        if not sym or not isinstance(snap, dict):
            continue
        try:
            px = float(snap.get("last_price"))
        except (TypeError, ValueError):
            continue
        if px <= 0:
            continue
        retained: dict[str, Any] = {
            "symbol": sym,
            "last_price": px,
            "source": str(snap.get("source") or "last_known"),
            "stale": True,
        }
        if snap.get("change") is not None:
            try:
                retained["change"] = float(snap["change"])
            except (TypeError, ValueError):
                pass
        if snap.get("change_pct") is not None:
            try:
                retained["change_pct"] = float(snap["change_pct"])
            except (TypeError, ValueError):
                pass
        if snap.get("instrument_key"):
            retained["instrument_key"] = str(snap["instrument_key"])
        retained["as_of"] = str(
            snap.get("as_of") or datetime.now(tz=timezone.utc).isoformat()
        )
        _LAST_INDEX_QUOTES[sym] = retained


def last_known_index_quotes(
    symbols: tuple[str, ...] = INDEX_STRIP_SYMBOLS,
) -> dict[str, dict[str, Any]]:
    """Return cached snapshots (never invents prices)."""
    out: dict[str, dict[str, Any]] = {}
    for sym in symbols:
        cached = _LAST_INDEX_QUOTES.get(sym)
        if cached and cached.get("last_price"):
            row = dict(cached)
            row["stale"] = True
            row["source"] = "last_known"
            out[sym] = row
    return out


async def fetch_live_index_quotes(
    symbols: tuple[str, ...] = INDEX_STRIP_SYMBOLS,
) -> dict[str, dict[str, Any]]:
    """Best-effort Upstox full-quote snapshots; falls back to LTP-only map."""
    uniq = tuple(str(s).upper() for s in symbols if s)
    if not uniq:
        return {}
    as_of = datetime.now(tz=timezone.utc).isoformat()
    try:
        from options_portal_broker.adapter import UpstoxAdapter

        broker = UpstoxAdapter()
        snaps: dict[str, dict[str, Any]] = {}
        try:
            snaps = await broker.get_quote_snapshots(uniq)
            missing = [s for s in uniq if s not in snaps]
            if missing:
                ltp_map = await broker.get_ltp_map(missing)
                for sym, px in ltp_map.items():
                    snaps[sym] = {
                        "symbol": sym,
                        "last_price": px,
                        "source": "upstox",
                    }
        finally:
            try:
                await broker.aclose()
            except Exception:  # noqa: BLE001
                logger.warning(
                    "broker aclose failed after index quotes", exc_info=True
                )

        out: dict[str, dict[str, Any]] = {}
        for sym, snap in snaps.items():
            try:
                px = float(snap["last_price"])
            except (KeyError, TypeError, ValueError):
                continue
            if px <= 0:
                continue
            row: dict[str, Any] = {
                "symbol": sym,
                "last_price": px,
                "source": str(snap.get("source") or "upstox"),
                "stale": False,
                "as_of": as_of,
            }
            if snap.get("instrument_key"):
                row["instrument_key"] = str(snap["instrument_key"])
            if snap.get("change") is not None:
                try:
                    row["change"] = float(snap["change"])
                except (TypeError, ValueError):
                    pass
            if snap.get("change_pct") is not None:
                try:
                    row["change_pct"] = float(snap["change_pct"])
                except (TypeError, ValueError):
                    pass
            out[sym] = row
        remember_index_quotes(out)
        return out
    except Exception:  # noqa: BLE001
        logger.exception("live index quotes unavailable")
        return {}


def _empty_index_row(sym: str) -> dict[str, Any]:
    return {
        "symbol": sym,
        "last_price": None,
        "change": None,
        "change_pct": None,
        "source": None,
        "stale": True,
    }


async def build_index_strip_payload() -> dict[str, Any]:
    """Payload for Watchlist Signals market status + NIFTY/SENSEX labels."""
    cfg = load_config()
    policy = SoftEodPolicy.from_config(cfg)
    clock = now_ist()
    trading_day = is_nse_trading_day(clock.date())
    open_now = is_cash_market_open(clock, policy=policy, trading_day=trading_day)
    status = "OPEN" if open_now else "CLOSE"

    # Quotes only — never invent. Prefer live Upstox (still valid after hours as
    # last traded); fall back to process cache. Mark stale when market is CLOSE.
    live = await fetch_live_index_quotes(INDEX_STRIP_SYMBOLS)
    cached = last_known_index_quotes(INDEX_STRIP_SYMBOLS)

    indices: list[dict[str, Any]] = []
    for sym in INDEX_STRIP_SYMBOLS:
        if sym in live:
            row = dict(live[sym])
            if not open_now:
                row["stale"] = True
            indices.append(row)
        elif sym in cached:
            indices.append(cached[sym])
        else:
            indices.append(_empty_index_row(sym))

    return {
        "market_status": status,
        "trading_day": trading_day,
        "session": {
            "open_ist": policy.open_ist,
            "close_ist": policy.close_ist,
            "tz": "Asia/Kolkata",
        },
        "as_of_ist": clock.isoformat(),
        "indices": indices,
        "poll_hint_sec": 15 if open_now else 120,
    }
