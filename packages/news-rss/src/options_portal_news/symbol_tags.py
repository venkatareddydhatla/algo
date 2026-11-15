"""Watchlist → keyword map and title/body symbol tagging heuristics."""

from __future__ import annotations

import re
from typing import Iterable, Sequence

from options_portal_domain.universe import Underlying

# Extra aliases beyond symbol/name tokens (India markets).
EXTRA_ALIASES: dict[str, list[str]] = {
    "NIFTY": ["nifty 50", "nifty50", "benchmark index"],
    "BANKNIFTY": ["bank nifty", "banknifty", "banking index"],
    "FINNIFTY": ["fin nifty", "finnifty"],
    "MIDCPNIFTY": ["midcap nifty", "midcpnifty", "nifty midcap"],
    "RELIANCE": ["ril", "reliance industries"],
    "TCS": ["tata consultancy", "tata consultancy services"],
    "INFY": ["infosys"],
    "HDFCBANK": ["hdfc bank"],
    "ICICIBANK": ["icici bank"],
    "SBIN": ["state bank of india", "sbi "],
    "BHARTIARTL": ["bharti airtel", "airtel"],
    "TATAMOTORS": ["tata motors"],
    "TATASTEEL": ["tata steel"],
}

_STOP = frozenset(
    {
        "ltd",
        "ltd.",
        "limited",
        "the",
        "and",
        "of",
        "india",
        "indian",
        "co",
        "co.",
        "corp",
        "corporation",
        "inc",
        "plc",
        "pvt",
        "private",
        "services",
        "solution",
        "solutions",
        "energy",
        "enterprises",
        "industries",
        "bank",  # too common alone — prefer "hdfc bank" via EXTRA
        "finance",
        "financial",
        "capital",
        "power",
        "ports",
        "green",
        "auto",
        "motors",
        "steel",
        "cement",
        "pharma",
        "hospital",
        "hospitals",
        "labs",
        "laboratories",
        "wam",
        "select",
        "next",
        "fpi",
    }
)


def build_symbol_keywords(
    underlyings: Sequence[Underlying] | Iterable[str],
    *,
    extras: dict[str, list[str]] | None = None,
) -> dict[str, list[str]]:
    """Build symbol → lowercase keyword list from universe rows or bare symbols."""
    mapping: dict[str, list[str]] = {}
    for item in underlyings:
        if isinstance(item, Underlying):
            sym = item.symbol.upper()
            name = item.name or ""
        else:
            sym = str(item).upper()
            name = ""
        kws: list[str] = []
        # Symbol token (hyphenated names: match with and without hyphen/space)
        sym_l = sym.lower()
        kws.append(sym_l)
        if "-" in sym_l:
            kws.append(sym_l.replace("-", " "))
            kws.append(sym_l.replace("-", ""))
        # Name phrases
        if name:
            cleaned = re.sub(r"[^A-Za-z0-9\s\-&.]", " ", name).lower()
            tokens = [t for t in cleaned.split() if t and t not in _STOP and len(t) > 2]
            if tokens:
                # Prefer multi-word company phrase when meaningful
                if len(tokens) >= 2:
                    kws.append(" ".join(tokens[:3]))
                kws.append(tokens[0])
        for extra in (extras or EXTRA_ALIASES).get(sym, []):
            kws.append(extra.lower())
        # Dedupe preserving order
        seen: set[str] = set()
        uniq: list[str] = []
        for k in kws:
            k = k.strip()
            if not k or k in seen:
                continue
            seen.add(k)
            uniq.append(k)
        mapping[sym] = uniq
    # Merge extras for symbols not in universe
    for sym, extra in (extras or EXTRA_ALIASES).items():
        if sym not in mapping:
            mapping[sym] = [e.lower() for e in extra]
    return mapping


def tag_symbols(text: str, mapping: dict[str, list[str]]) -> list[str]:
    """Tag symbols whose keywords appear in text (word-boundary aware for short keys)."""
    if not text or not mapping:
        return []
    lower = text.lower()
    hits: list[str] = []
    for sym, kws in mapping.items():
        for kw in kws:
            if not kw:
                continue
            if len(kw) <= 3 or kw.isalnum() and len(kw) <= 5:
                # Short ticker-like: require word boundary
                if re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", lower):
                    hits.append(sym)
                    break
            elif kw in lower:
                hits.append(sym)
                break
    return hits
