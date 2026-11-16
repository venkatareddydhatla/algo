"""Symbol → Upstox instrument_key mapping (indices + common equities).

Equity keys use ISIN form ``NSE_EQ|<ISIN>``. Unknown symbols fall back to
synthetic candles unless an override map is provided.
"""

from __future__ import annotations

import json
import os
from typing import Mapping

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

# Curated equity ISINs for frequent F&O underlyings (expand via override env).
EQUITY_INSTRUMENT_KEYS: dict[str, str] = {
    "RELIANCE": "NSE_EQ|INE002A01018",
    "TCS": "NSE_EQ|INE467B01029",
    "INFY": "NSE_EQ|INE009A01021",
    "HDFCBANK": "NSE_EQ|INE040A01034",
    "ICICIBANK": "NSE_EQ|INE090A01021",
    "SBIN": "NSE_EQ|INE062A01020",
    "ITC": "NSE_EQ|INE154A01025",
    "BHARTIARTL": "NSE_EQ|INE397D01024",
    "LT": "NSE_EQ|INE018A01030",
    "AXISBANK": "NSE_EQ|INE238A01034",
    "KOTAKBANK": "NSE_EQ|INE237A01028",
    "WIPRO": "NSE_EQ|INE075A01022",
    "HCLTECH": "NSE_EQ|INE860A01027",
    "SUNPHARMA": "NSE_EQ|INE044A01036",
    "MARUTI": "NSE_EQ|INE585B01010",
    "TATAMOTORS": "NSE_EQ|INE155A01022",
    "TATASTEEL": "NSE_EQ|INE081A01020",
    "NTPC": "NSE_EQ|INE733E01010",
    "ONGC": "NSE_EQ|INE213A01029",
    "POWERGRID": "NSE_EQ|INE752E01010",
    "BAJFINANCE": "NSE_EQ|INE296A01024",
    "BAJAJFINSV": "NSE_EQ|INE918I01026",
    "ADANIENT": "NSE_EQ|INE423A01024",
    "ADANIPORTS": "NSE_EQ|INE742F01042",
    "ASIANPAINT": "NSE_EQ|INE021A01026",
    "ULTRACEMCO": "NSE_EQ|INE481G01011",
    "TITAN": "NSE_EQ|INE280A01028",
    "NESTLEIND": "NSE_EQ|INE239A01024",
    "COALINDIA": "NSE_EQ|INE522F01014",
    "JSWSTEEL": "NSE_EQ|INE019A01038",
    "TECHM": "NSE_EQ|INE669C01036",
    "HINDUNILVR": "NSE_EQ|INE030A01027",
    "M&M": "NSE_EQ|INE101A01026",
    "CIPLA": "NSE_EQ|INE059A01026",
    "DRREDDY": "NSE_EQ|INE089A01031",
    "BPCL": "NSE_EQ|INE029A01011",
    "HEROMOTOCO": "NSE_EQ|INE158A01026",
    "EICHERMOT": "NSE_EQ|INE066A01021",
    "GRASIM": "NSE_EQ|INE047A01021",
    "INDUSINDBK": "NSE_EQ|INE095A01012",
    "APOLLOHOSP": "NSE_EQ|INE437A01024",
    "DIVISLAB": "NSE_EQ|INE361B01024",
    "SBILIFE": "NSE_EQ|INE123W01016",
    "HDFCLIFE": "NSE_EQ|INE795G01014",
    "BAJAJ-AUTO": "NSE_EQ|INE917I01010",
    "SYRMA": "NSE_EQ|INE0DYJ01015",
    # 360 ONE WAM (formerly IIFL Wealth) — missing key forced synthetic ~1000 prints
    "360ONE": "NSE_EQ|INE726L01019",
}


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


def default_instrument_map() -> dict[str, str]:
    merged = {**EQUITY_INSTRUMENT_KEYS, **INDEX_INSTRUMENT_KEYS}
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
    return default_instrument_map().get(sym)
