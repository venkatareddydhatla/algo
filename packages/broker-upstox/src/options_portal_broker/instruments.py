"""Symbol → Upstox instrument_key mapping (indices + NSE F&O equities).

Equity keys use ISIN form ``NSE_EQ|<ISIN>`` from the checked-in map
``config/universe/nse_eq_instrument_keys.csv`` (sourced from Upstox NSE
instruments master). Unknown symbols return ``None`` so callers fall back
to synthetic candles only when live MD is unavailable — never silently reuse
another symbol's quote.
"""

from __future__ import annotations

import csv
import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Mapping

logger = logging.getLogger(__name__)

# NSE index underlyings (weekly F&O). Keys match Upstox instrument master.
INDEX_INSTRUMENT_KEYS: dict[str, str] = {
    "NIFTY": "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "FINNIFTY": "NSE_INDEX|Nifty Fin Service",
    "MIDCPNIFTY": "NSE_INDEX|NIFTY MID SELECT",
    "NIFTYNXT50": "NSE_INDEX|Nifty Next 50",
    "NIFTYFPI": "NSE_INDEX|Nifty FPI 150",
    "INDIAVIX": "NSE_INDEX|India VIX",
}

# Renames / legacy tradingsymbols → current NSE cash symbol in the CSV map.
EQUITY_SYMBOL_ALIASES: dict[str, str] = {
    "TATAMOTORS": "TMPV",
}


def _candidate_key_csv_paths() -> list[Path]:
    """Resolve checked-in instrument key CSV from common working directories."""
    here = Path(__file__).resolve()
    return [
        Path("config/universe/nse_eq_instrument_keys.csv"),
        Path("universe/nse_eq_instrument_keys.csv"),
        here.parents[4] / "config" / "universe" / "nse_eq_instrument_keys.csv",
        # Editable install / nested layouts
        here.parents[5] / "config" / "universe" / "nse_eq_instrument_keys.csv",
    ]


def _load_equity_keys_csv() -> dict[str, str]:
    """Load ``symbol → NSE_EQ|ISIN`` from the checked-in F&O equity key CSV."""
    path: Path | None = None
    for candidate in _candidate_key_csv_paths():
        if candidate.is_file():
            path = candidate
            break
    if path is None:
        logger.error(
            "nse_eq_instrument_keys.csv not found — equity LTP/candles will "
            "fail open for unmapped symbols (synthetic fallback only)"
        )
        return {}

    out: dict[str, str] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            sym = str(raw.get("symbol") or "").strip().upper()
            ikey = str(raw.get("instrument_key") or "").strip()
            if not sym or not ikey:
                continue
            if not ikey.startswith("NSE_EQ|"):
                logger.warning(
                    "Skipping non-equity instrument_key for %s: %s", sym, ikey
                )
                continue
            out[sym] = ikey
    if not out:
        logger.error("nse_eq_instrument_keys.csv at %s loaded 0 rows", path)
    else:
        logger.debug("Loaded %d equity instrument keys from %s", len(out), path)
    return out


@lru_cache(maxsize=1)
def equity_instrument_keys() -> dict[str, str]:
    """Cached equity ISIN map (includes alias targets as their own rows)."""
    return dict(_load_equity_keys_csv())


def clear_instrument_key_cache() -> None:
    """Test helper — drop cached CSV map."""
    equity_instrument_keys.cache_clear()
    default_instrument_map.cache_clear()


def _parse_override_map(raw: str) -> dict[str, str]:
    """Parse ``UPSTOX_INSTRUMENT_MAP`` JSON object ``{SYMBOL: instrument_key}``."""
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in data.items():
        sym = str(key).strip().upper()
        ikey = str(value).strip()
        if sym and ikey:
            out[sym] = ikey
    return out


@lru_cache(maxsize=1)
def default_instrument_map() -> dict[str, str]:
    """Indices + F&O equities + ``UPSTOX_INSTRUMENT_MAP`` env overrides."""
    merged = {**INDEX_INSTRUMENT_KEYS, **equity_instrument_keys()}
    # Apply aliases only when the alias itself is not already mapped
    for alias, target in EQUITY_SYMBOL_ALIASES.items():
        if alias not in merged and target in merged:
            merged[alias] = merged[target]
    merged.update(_parse_override_map(os.environ.get("UPSTOX_INSTRUMENT_MAP", "")))
    return merged


def resolve_instrument_key(
    symbol: str,
    *,
    overrides: Mapping[str, str] | None = None,
) -> str | None:
    """Return Upstox instrument_key for ``symbol``, or None if unknown."""
    sym = symbol.strip().upper()
    if not sym:
        return None
    if overrides and sym in overrides:
        return overrides[sym]
    mapping = default_instrument_map()
    if sym in mapping:
        return mapping[sym]
    # Alias lookup when CSV row missing for legacy name
    target = EQUITY_SYMBOL_ALIASES.get(sym)
    if target and target in mapping:
        return mapping[target]
    return None
