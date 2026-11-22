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
# Curated CSV names dropped from the active Signals / scan watch (CSV unchanged).
KV_KEY_EXCLUDED_CURATED = "watchlist.excluded_curated_symbols"

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


def filter_excluded_symbols(
    symbols: list[str],
    excluded: list[str] | None,
) -> list[str]:
    """Drop tickers present in the exclusion list (order preserved)."""
    ban = {normalize_symbol(s) for s in (excluded or []) if normalize_symbol(s)}
    if not ban:
        return [normalize_symbol(s) for s in symbols if normalize_symbol(s)]
    return [normalize_symbol(s) for s in symbols if normalize_symbol(s) and normalize_symbol(s) not in ban]


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


def _kv_or_none(kv: Any | None = None) -> Any | None:
    if kv is not None:
        return kv
    try:
        from options_portal_persistence.kv_store import KvStore

        store = KvStore()
    except Exception:  # noqa: BLE001
        logger.exception("custom_watchlist: kv unavailable")
        return None
    if store is None or not getattr(store, "available", False):
        return None
    return store


def load_excluded_curated_symbols(*, kv: Any | None = None) -> list[str]:
    """Load curated names excluded from the active watch (empty when unavailable)."""
    store = _kv_or_none(kv)
    if store is None:
        return []
    raw = store.get(KV_KEY_EXCLUDED_CURATED, None)
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return merge_watchlist_with_customs([], [str(x) for x in raw])


def save_excluded_curated_symbols(symbols: list[str], *, kv: Any | None = None) -> bool:
    """Replace the curated exclusion list. Dedupes and uppercases."""
    store = _kv_or_none(kv)
    if store is None:
        return False
    cleaned = merge_watchlist_with_customs([], symbols)
    return bool(store.set(KV_KEY_EXCLUDED_CURATED, cleaned))


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
    excluded = set(load_excluded_curated_symbols(kv=kv))
    # Curated name previously dropped from active watch → restore (CSV untouched).
    if sym in base and sym in excluded:
        restored = restore_excluded_curated_symbol(sym, kv=kv)
        return {
            **restored,
            "instrument_key": instrument_key,
            "restored_via": "add_custom",
        }
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
    # Drop leftover pine.signal.* (hybrid-era HOLD + old ts) so the next 15m
    # scan / GET /signals builds Reversal Pro Action+Trend (no HOLD linger).
    cleared_stale = clear_custom_symbol_signal_kv(sym, kv=kv)
    return {
        "ok": True,
        "status": "added",
        "symbol": sym,
        "message": f"{sym} added to custom watchlist (persisted in SQLite kv)",
        "instrument_key": instrument_key,
        "customs": next_list,
        "stale_pine_kv_cleared": cleared_stale,
    }


def clear_custom_symbol_signal_kv(
    symbol: str,
    *,
    kv: Any | None = None,
) -> bool:
    """Delete ``pine.signal.{SYM}`` so customs never keep hybrid HOLD/timestamp."""
    sym = normalize_symbol(symbol)
    if not sym:
        return False
    store = kv
    if store is None:
        try:
            from options_portal_persistence.kv_store import KvStore

            store = KvStore()
        except Exception:  # noqa: BLE001
            return False
    if store is None or not getattr(store, "available", False):
        return False
    try:
        key = f"pine.signal.{sym}"
        had = store.get(key) is not None
        store.delete(key)
        return bool(had)
    except Exception:  # noqa: BLE001
        logger.exception("custom_watchlist: clear pine.signal.%s failed", sym)
        return False


def remove_custom_symbol(
    symbol: str,
    *,
    kv: Any | None = None,
) -> dict[str, Any]:
    """Remove one ticker from the custom list (never edits the curated CSV).

    Also clears ``pine.signal.{SYM}`` so GET /signals cannot linger a stale Action.
    """
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
                "(use DELETE /api/v1/watchlist/active/{symbol} for curated names)"
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
    cleared = clear_custom_symbol_signal_kv(sym, kv=kv)
    return {
        "ok": True,
        "status": "removed",
        "symbol": sym,
        "message": f"{sym} removed from custom watchlist",
        "customs": next_list,
        "source": "custom",
        "pine_signal_kv_cleared": cleared,
    }


def exclude_curated_symbol(
    symbol: str,
    *,
    base_watchlist: list[str] | None = None,
    kv: Any | None = None,
) -> dict[str, Any]:
    """Drop a curated CSV name from the active watch via exclusion KV (CSV untouched)."""
    sym = normalize_symbol(symbol)
    if not sym:
        return {"ok": False, "status": "error", "symbol": "", "message": "symbol required"}

    base = {normalize_symbol(s) for s in (base_watchlist or [])}
    if not base:
        return {
            "ok": False,
            "status": "error",
            "symbol": sym,
            "message": "curated base watchlist unavailable",
        }
    if sym not in base:
        return {
            "ok": False,
            "status": "not_curated",
            "symbol": sym,
            "message": f"{sym} is not on the curated TradingView list",
        }

    # If also on custom list, drop the custom entry first (dedupe path).
    customs = load_custom_symbols(kv=kv)
    if sym in customs:
        remove_custom_symbol(sym, kv=kv)

    excluded = load_excluded_curated_symbols(kv=kv)
    if sym in excluded:
        cleared = clear_custom_symbol_signal_kv(sym, kv=kv)
        return {
            "ok": True,
            "status": "already_excluded",
            "symbol": sym,
            "message": f"{sym} is already excluded from the active watch",
            "excluded": excluded,
            "source": "curated",
            "pine_signal_kv_cleared": cleared,
        }

    next_ex = excluded + [sym]
    if not save_excluded_curated_symbols(next_ex, kv=kv):
        return {
            "ok": False,
            "status": "error",
            "symbol": sym,
            "message": "failed to persist curated exclusion (SQLite kv unavailable)",
            "excluded": excluded,
        }
    cleared = clear_custom_symbol_signal_kv(sym, kv=kv)
    return {
        "ok": True,
        "status": "excluded",
        "symbol": sym,
        "message": (
            f"{sym} removed from active watch (curated CSV unchanged; "
            "restore via Add symbol or DELETE /api/v1/watchlist/exclusions/{symbol})"
        ),
        "excluded": next_ex,
        "source": "curated",
        "pine_signal_kv_cleared": cleared,
        "kv_key": KV_KEY_EXCLUDED_CURATED,
    }


def restore_excluded_curated_symbol(
    symbol: str,
    *,
    kv: Any | None = None,
) -> dict[str, Any]:
    """Remove a ticker from the curated exclusion list so it rejoins the active watch."""
    sym = normalize_symbol(symbol)
    if not sym:
        return {"ok": False, "status": "error", "symbol": "", "message": "symbol required"}

    excluded = load_excluded_curated_symbols(kv=kv)
    if sym not in excluded:
        return {
            "ok": False,
            "status": "not_excluded",
            "symbol": sym,
            "message": f"{sym} is not on the curated exclusion list",
            "excluded": excluded,
        }

    next_ex = [s for s in excluded if s != sym]
    if not save_excluded_curated_symbols(next_ex, kv=kv):
        return {
            "ok": False,
            "status": "error",
            "symbol": sym,
            "message": "failed to persist restore (SQLite kv unavailable)",
            "excluded": excluded,
        }
    cleared = clear_custom_symbol_signal_kv(sym, kv=kv)
    return {
        "ok": True,
        "status": "restored",
        "symbol": sym,
        "message": f"{sym} restored to the active curated watch",
        "excluded": next_ex,
        "source": "curated",
        "pine_signal_kv_cleared": cleared,
        "kv_key": KV_KEY_EXCLUDED_CURATED,
    }


def remove_from_active_watch(
    symbol: str,
    *,
    base_watchlist: list[str] | None = None,
    kv: Any | None = None,
) -> dict[str, Any]:
    """Remove one symbol from the active Signals / scan watch.

    - Custom → ``remove_custom_symbol`` + clear ``pine.signal.{SYM}``
    - Curated → exclusion list (CSV unchanged) + clear ``pine.signal.{SYM}``
    """
    sym = normalize_symbol(symbol)
    if not sym:
        return {"ok": False, "status": "error", "symbol": "", "message": "symbol required"}

    customs = set(load_custom_symbols(kv=kv))
    base = {normalize_symbol(s) for s in (base_watchlist or [])}
    excluded = set(load_excluded_curated_symbols(kv=kv))

    # Prefer custom removal when the ticker is portal-added (not on curated base,
    # or on both — customs of curated names shouldn't normally exist).
    if sym in customs and sym not in base:
        return remove_custom_symbol(sym, kv=kv)

    if sym in base and sym not in excluded:
        return exclude_curated_symbol(sym, base_watchlist=list(base), kv=kv)

    if sym in customs:
        return remove_custom_symbol(sym, kv=kv)

    if sym in excluded:
        cleared = clear_custom_symbol_signal_kv(sym, kv=kv)
        return {
            "ok": True,
            "status": "already_excluded",
            "symbol": sym,
            "message": f"{sym} is already off the active watch",
            "source": "curated",
            "pine_signal_kv_cleared": cleared,
        }

    return {
        "ok": False,
        "status": "not_on_watch",
        "symbol": sym,
        "message": f"{sym} is not on the active watchlist",
    }
