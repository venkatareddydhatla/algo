"""NSE F&O underlying universe loader (checked-in CSV + config aliases)."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

# Major NSE index F&O underlyings (weekly expiry by default).
INDEX_SYMBOLS = frozenset(
    {
        "NIFTY",
        "BANKNIFTY",
        "FINNIFTY",
        "MIDCPNIFTY",
        "NIFTYNXT50",
        "NIFTYFPI",
    }
)

DEMO_SYMBOLS = ("NIFTY", "BANKNIFTY", "RELIANCE", "TCS")

# Built-in aliases resolved relative to repo root / CONFIG_PATH parent.
UNIVERSE_ALIASES: dict[str, str] = {
    "nse_fno": "config/universe/nse_fno_underlyings.csv",
    "nse_fno_demo": "demo",  # sentinel — use DEMO_SYMBOLS
}


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
    """Subset of the watchlist that worker/TA loops should scan this cycle.

    Full watchlist remains visible in the portal; scanning is capped to avoid
    CPU blow-ups on ~200 underlyings.
    """
    cfg = trading_cfg or {}
    max_active = cfg.get("max_active_symbols")
    if max_active is None:
        max_active = cfg.get("scan", {}).get("max_active_symbols", 20)
    try:
        limit = int(max_active)
    except (TypeError, ValueError):
        limit = 20
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
