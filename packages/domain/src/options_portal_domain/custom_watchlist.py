"""User-appended equity symbols on top of the curated universe CSV.

Persists in SQLite ``kv_store`` (same local profile as adaptive hybrid delta).
Curated ``volatile_fno50`` TradingView list (or whatever ``trading.universe`` resolves) stays the
base list; customs are merged with dedupe so the 15m bar-close scan never
scores the same ticker twice.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from options_portal_domain.universe import is_index_underlying

logger = logging.getLogger(__name__)

KV_KEY_CUSTOM_SYMBOLS = "watchlist.custom_equity_symbols"

# NSE cash tickers: letters, digits, & (M&M), hyphen rare — keep tight.
_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9&-]{0,19}$")


def normalize_symbol(symbol: str | None) -> str:
    return str(symbol or "").strip().upper()


def merge_watchlist_with_customs(
    base: list[str],
    customs: list[str] | None,
) -> list[str]:
    """Curated base first, then custom equities not already present (case-insensitive)."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in list(base or []) + list(customs or []):
        sym = normalize_symbol(raw)
        if not sym or sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
    return out


def load_custom_symbols(*, kv: Any | None = None) -> list[str]:
    """Load persisted custom tickers from SQLite KV (empty when unavailable)."""
    store = kv
    if store is None:
        try:
            from options_portal_persistence.kv_store import KvStore

            store = KvStore()
        except Exception:  # noqa: BLE001
            logger.exception("custom_watchlist: kv unavailable")
            return []
    if store is None or not getattr(store, "available", False):
        return []
    raw = store.get(KV_KEY_CUSTOM_SYMBOLS, None)
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        sym = normalize_symbol(item)
        if not sym or sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
    return out


def save_custom_symbols(symbols: list[str], *, kv: Any | None = None) -> bool:
    """Replace the custom list. Dedupes and uppercases."""
    store = kv
    if store is None:
        try:
            from options_portal_persistence.kv_store import KvStore

            store = KvStore()
        except Exception:  # noqa: BLE001
            return False
    if store is None or not getattr(store, "available", False):
        return False
    cleaned = merge_watchlist_with_customs([], symbols)
    return bool(store.set(KV_KEY_CUSTOM_SYMBOLS, cleaned))


def validate_custom_equity_candidate(
    symbol: str,
    *,
    instrument_key: str | None,
) -> str | None:
    """Return an error message when ``symbol`` must not be added, else None.

    Caller resolves ``instrument_key`` via Upstox helpers before calling.
    """
    sym = normalize_symbol(symbol)
    if not sym:
        return "symbol required"
    if not _SYMBOL_RE.match(sym):
        return f"invalid symbol ticker: {sym!r} (use NSE equity tradingsymbol)"
    if is_index_underlying(sym, instrument_key=instrument_key):
        return f"{sym} is an index — equity stocks only (no NIFTY/BANKNIFTY/etc.)"
    if not instrument_key:
        return (
            f"unknown NSE equity symbol: {sym} "
            "(no instrument_key in nse_eq_instrument_keys map)"
        )
    key_u = str(instrument_key).strip().upper()
    if not key_u.startswith("NSE_EQ|") and not key_u.startswith("NSE_EQ:"):
        return (
            f"{sym} resolves to non-equity instrument_key={instrument_key!r} "
            "— equity stocks only"
        )
    return None


def add_custom_symbol(
    symbol: str,
    *,
    instrument_key: str | None,
    base_watchlist: list[str] | None = None,
    kv: Any | None = None,
) -> dict[str, Any]:
    """Validate + persist one custom equity. Dedupes against curated base.

    Returns a status dict:
    - ``added`` — stored and will join the next scan
    - ``already_custom`` — already on the custom list
    - ``already_on_curated`` — on curated TradingView list (or current base); not double-stored
    - ``error`` — rejected (indices / unknown / invalid)
    """
    sym = normalize_symbol(symbol)
    err = validate_custom_equity_candidate(sym, instrument_key=instrument_key)
    if err:
        return {
            "ok": False,
            "status": "error",
            "symbol": sym,
            "message": err,
            "instrument_key": instrument_key,
        }

    base = {normalize_symbol(s) for s in (base_watchlist or [])}
    if sym in base:
        return {
            "ok": True,
            "status": "already_on_curated",
            "symbol": sym,
            "message": (
                f"{sym} is already on the curated watchlist — "
                "not double-scanned; custom list unchanged"
            ),
            "instrument_key": instrument_key,
        }

    current = load_custom_symbols(kv=kv)
    if sym in current:
        return {
            "ok": True,
            "status": "already_custom",
            "symbol": sym,
            "message": f"{sym} is already on the custom watchlist",
            "instrument_key": instrument_key,
            "customs": current,
        }

    next_list = current + [sym]
    if not save_custom_symbols(next_list, kv=kv):
        return {
            "ok": False,
            "status": "error",
            "symbol": sym,
            "message": "failed to persist custom symbol (SQLite kv unavailable)",
            "instrument_key": instrument_key,
        }
    return {
        "ok": True,
        "status": "added",
        "symbol": sym,
        "message": f"{sym} added to custom watchlist (persisted in SQLite kv)",
        "instrument_key": instrument_key,
        "customs": next_list,
    }


def remove_custom_symbol(
    symbol: str,
    *,
    kv: Any | None = None,
) -> dict[str, Any]:
    """Remove one ticker from the custom list (never edits the curated CSV)."""
    sym = normalize_symbol(symbol)
    if not sym:
        return {"ok": False, "status": "error", "symbol": "", "message": "symbol required"}

    current = load_custom_symbols(kv=kv)
    if sym not in current:
        return {
            "ok": False,
            "status": "not_custom",
            "symbol": sym,
            "message": (
                f"{sym} is not on the custom list "
                "(curated TradingView names cannot be removed here)"
            ),
            "customs": current,
        }

    next_list = [s for s in current if s != sym]
    if not save_custom_symbols(next_list, kv=kv):
        return {
            "ok": False,
            "status": "error",
            "symbol": sym,
            "message": "failed to persist removal (SQLite kv unavailable)",
            "customs": current,
        }
    return {
        "ok": True,
        "status": "removed",
        "symbol": sym,
        "message": f"{sym} removed from custom watchlist",
        "customs": next_list,
    }
