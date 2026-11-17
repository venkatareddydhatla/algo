"""NSE F&O underlying universe loader (checked-in CSV + config aliases)."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

# Major NSE/BSE index underlyings (F&O + cash index quotes).
# Equity dry-run must NEVER open/hold these as stock paper positions.
INDEX_SYMBOLS = frozenset(
    {
        "NIFTY",
        "BANKNIFTY",
        "FINNIFTY",
        "MIDCPNIFTY",
        "NIFTYNXT50",
        "NIFTYFPI",
        "SENSEX",
        "INDIAVIX",
        "BANKEX",
    }
)

# Instrument-key segment prefixes used by Upstox (and similar) for indices.
_INDEX_KEY_SEGMENTS = ("NSE_INDEX", "BSE_INDEX")

DEMO_SYMBOLS = ("NIFTY", "BANKNIFTY", "RELIANCE", "TCS")

# Built-in aliases resolved relative to repo root / CONFIG_PATH parent.
UNIVERSE_ALIASES: dict[str, str] = {
    "nse_fno": "config/universe/nse_fno_underlyings.csv",
    "nse_fno_demo": "demo",  # sentinel — use DEMO_SYMBOLS
}


def instrument_key_is_index(instrument_key: str | None) -> bool:
    """True when instrument_key segment is NSE_INDEX / BSE_INDEX (``|`` or ``:``)."""
    raw = str(instrument_key or "").strip().upper()
    if not raw:
        return False
    # Forms: NSE_INDEX|Nifty 50 · NSE_INDEX:Nifty 50 · NSE_INDEX|...
    segment = raw.replace(":", "|").split("|", 1)[0].strip()
    return segment in _INDEX_KEY_SEGMENTS or any(
        seg in raw for seg in _INDEX_KEY_SEGMENTS
    )


def is_index_underlying(
    symbol: str,
    *,
    instrument_key: str | None = None,
    kind: str | None = None,
) -> bool:
    """True for index underlyings that must not use equity stock entry/exit book.

    Preference order:
    1. Explicit universe ``kind=index``
    2. Instrument-key segment ``NSE_INDEX`` / ``BSE_INDEX``
    3. Known denylist (``INDEX_SYMBOLS``) + light name patterns
    """
    sym = str(symbol or "").strip().upper()
    if not sym:
        return False
    kind_norm = str(kind or "").strip().lower()
    if kind_norm == "index":
        return True
    if instrument_key_is_index(instrument_key):
        return True
    if sym in INDEX_SYMBOLS:
        return True
    # Light patterns: avoid matching ordinary equities (e.g. not *BANK*).
    if sym.endswith("NIFTY") or sym.startswith("NIFTY"):
        return True
    if sym in {"SENSEX", "BANKEX"} or sym.endswith("VIX"):
        return True
    return False


@dataclass(frozen=True)
class Underlying:
    symbol: str
    name: str = ""
    kind: str = "equity"  # index | equity
    expiry_rule: str = "monthly"  # weekly | monthly
    lot_size: int | None = None


def default_universe_csv() -> Path:
    """Resolve default checked-in universe path from common working directories."""
    candidates = [
        Path("config/universe/nse_fno_underlyings.csv"),
        Path(__file__).resolve().parents[4] / "config" / "universe" / "nse_fno_underlyings.csv",
    ]
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def load_universe_csv(path: str | Path) -> list[Underlying]:
    """Load underlyings from a CSV with columns symbol[, name, kind, expiry_rule, lot_size]."""
    csv_path = Path(path)
    if not csv_path.exists():
        raise FileNotFoundError(f"Universe CSV not found: {csv_path}")
    rows: list[Underlying] = []
    seen: set[str] = set()
    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = {f.strip().lower() for f in (reader.fieldnames or [])}
        if "symbol" not in fieldnames:
            raise ValueError(f"Universe CSV missing 'symbol' column: {csv_path}")
        for raw in reader:
            symbol = (raw.get("symbol") or "").strip().upper()
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            kind = (raw.get("kind") or "").strip().lower()
            if not kind:
                kind = "index" if symbol in INDEX_SYMBOLS else "equity"
            expiry = (raw.get("expiry_rule") or "").strip().lower()
            if not expiry:
                expiry = "weekly" if kind == "index" else "monthly"
            lot_raw = (raw.get("lot_size") or "").strip()
            lot = int(lot_raw) if lot_raw.isdigit() else None
            rows.append(
                Underlying(
                    symbol=symbol,
                    name=(raw.get("name") or "").strip(),
                    kind=kind,
                    expiry_rule=expiry,
                    lot_size=lot,
                )
            )
    return rows


def resolve_universe_path(spec: str, *, config_dir: Path | None = None) -> Path | None:
    """Map universe alias or relative path to a CSV path. Returns None for demo sentinel."""
    key = spec.strip()
    if key in UNIVERSE_ALIASES:
        mapped = UNIVERSE_ALIASES[key]
        if mapped == "demo":
            return None
        key = mapped
    path = Path(key)
    if not path.is_absolute() and config_dir is not None:
        # Prefer paths relative to repo root (parent of config/) when given config file dir
        repo_root = config_dir.parent if config_dir.name == "config" else config_dir
        candidate = repo_root / path
        if candidate.exists():
            return candidate
    if path.exists():
        return path
    # Fall back to default location for nse_fno alias miss
    if spec.strip() == "nse_fno":
        return default_universe_csv()
    return path


def symbols_from_underlyings(rows: Iterable[Underlying]) -> list[str]:
    return [u.symbol for u in rows]


def resolve_watchlist_symbols(
    trading_cfg: dict[str, Any] | None,
    *,
    config_dir: Path | None = None,
) -> list[str]:
    """Resolve trading.symbols / trading.universe into a symbol list.

    Supported forms:
    - symbols: [NIFTY, ...]  (explicit list)
    - symbols: nse_fno | symbols: universe:nse_fno
    - universe: nse_fno  (with optional symbols_demo for tests)
    - symbols_file: path/to.csv
    """
    cfg = trading_cfg or {}
    demo = cfg.get("symbols_demo")
    if isinstance(demo, list) and demo and cfg.get("use_symbols_demo"):
        return [str(s).upper() for s in demo]

    # Explicit file wins when set
    symbols_file = cfg.get("symbols_file")
    if symbols_file:
        rows = load_universe_csv(resolve_universe_path(str(symbols_file), config_dir=config_dir) or symbols_file)
        return symbols_from_underlyings(rows)

    universe = cfg.get("universe")
    symbols = cfg.get("symbols")

    # symbols: "nse_fno" or "universe:nse_fno"
    if isinstance(symbols, str):
        spec = symbols
        if spec.startswith("universe:"):
            spec = spec.split(":", 1)[1]
        if spec in {"demo", "nse_fno_demo"} or spec == "symbols_demo":
            return [str(s).upper() for s in (demo or list(DEMO_SYMBOLS))]
        path = resolve_universe_path(spec, config_dir=config_dir)
        if path is None:
            return [str(s).upper() for s in (demo or list(DEMO_SYMBOLS))]
        return symbols_from_underlyings(load_universe_csv(path))

    if universe:
        spec = str(universe)
        if spec.startswith("universe:"):
            spec = spec.split(":", 1)[1]
        if spec in {"demo", "nse_fno_demo"}:
            return [str(s).upper() for s in (demo or list(DEMO_SYMBOLS))]
        path = resolve_universe_path(spec, config_dir=config_dir)
        if path is None:
            return [str(s).upper() for s in (demo or list(DEMO_SYMBOLS))]
        return symbols_from_underlyings(load_universe_csv(path))

    if isinstance(symbols, list) and symbols:
        return [str(s).upper() for s in symbols]

    # Default: full NSE F&O if CSV present, else demo
    default_path = default_universe_csv()
    if default_path.exists():
        return symbols_from_underlyings(load_universe_csv(default_path))
    return list(DEMO_SYMBOLS)


def active_scan_symbols(
    watchlist: list[str],
    trading_cfg: dict[str, Any] | None = None,
) -> list[str]:
    """Symbols the worker/TA loop should score this cycle.

    ``max_active_symbols`` of ``0`` or ``null`` means unlimited (full watchlist).
    A positive integer caps the scan to the first N symbols. Batching is controlled
    separately by ``scan_batch_size`` so one machine can finish a full NSE F&O
    pass without hanging.
    """
    cfg = trading_cfg or {}
    max_active = cfg.get("max_active_symbols", None)
    if max_active is None and "max_active_symbols" not in cfg:
        max_active = cfg.get("scan", {}).get("max_active_symbols", 0)
    if max_active is None:
        return list(watchlist)
    try:
        limit = int(max_active)
    except (TypeError, ValueError):
        limit = 0
    if limit <= 0:
        return list(watchlist)
    return list(watchlist[:limit])


def scan_batch_size(trading_cfg: dict[str, Any] | None = None) -> int:
    cfg = trading_cfg or {}
    raw = cfg.get("scan_batch_size")
    if raw is None:
        raw = cfg.get("scan", {}).get("batch_size", 8)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = 8
    return max(1, n)
