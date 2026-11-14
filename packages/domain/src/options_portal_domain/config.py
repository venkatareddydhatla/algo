"""Load non-secret YAML config; secrets come from environment."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from options_portal_domain.universe import (
    DEMO_SYMBOLS,
    active_scan_symbols,
    resolve_watchlist_symbols,
    scan_batch_size,
)


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load application config from CONFIG_PATH or the given path."""
    cfg_path = Path(path or os.environ.get("CONFIG_PATH", "config/local.yaml"))
    if not cfg_path.exists():
        # Fall back to default.yaml shipped in repo
        alt = Path("config/default.yaml")
        if alt.exists():
            cfg_path = alt
        else:
            return _default_config()
    with cfg_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    # Env overrides for safety-critical knobs
    mode = os.environ.get("TRADING_MODE")
    if mode:
        data.setdefault("trading", {})["mode"] = mode
    halt = os.environ.get("TRADING_HALT")
    if halt is not None:
        data.setdefault("trading", {})["halt"] = halt.lower() in {"1", "true", "yes"}
    # Resolve universe aliases into concrete symbol lists for consumers
    trading = data.setdefault("trading", {})
    config_dir = cfg_path.parent
    watchlist = resolve_watchlist_symbols(trading, config_dir=config_dir)
    trading["symbols_resolved"] = watchlist
    trading["symbols_watchlist"] = watchlist
    trading["symbols_active"] = active_scan_symbols(watchlist, trading)
    trading["scan_batch_size_resolved"] = scan_batch_size(trading)
    # Keep UI/admin readable: if symbols was an alias string, expose resolved list
    # under symbols_resolved; leave original symbols / universe keys intact.
    return data


def trading_mode(config: dict[str, Any] | None = None) -> str:
    cfg = config or load_config()
    return str(cfg.get("trading", {}).get("mode", "dry_run"))


def is_halted(config: dict[str, Any] | None = None) -> bool:
    cfg = config or load_config()
    return bool(cfg.get("trading", {}).get("halt", False))


def watchlist_symbols(config: dict[str, Any] | None = None) -> list[str]:
    """Full portal watchlist (may be the entire NSE F&O universe)."""
    cfg = config or load_config()
    trading = cfg.get("trading", {})
    resolved = trading.get("symbols_resolved") or trading.get("symbols_watchlist")
    if isinstance(resolved, list) and resolved:
        return [str(s).upper() for s in resolved]
    return resolve_watchlist_symbols(trading)


def scan_symbols(config: dict[str, Any] | None = None) -> list[str]:
    """Symbols the worker should actively scan (full watchlist when uncapped)."""
    cfg = config or load_config()
    trading = cfg.get("trading", {})
    active = trading.get("symbols_active")
    if isinstance(active, list) and active:
        return [str(s).upper() for s in active]
    return active_scan_symbols(watchlist_symbols(cfg), trading)


def _default_config() -> dict[str, Any]:
    return {
        "app": {"name": "options-portal", "timezone": "Asia/Kolkata"},
        "trading": {
            "mode": "dry_run",
            "halt": False,
            "timeframe": "15m",
            "universe": "nse_fno",
            "symbols_demo": list(DEMO_SYMBOLS),
            "max_active_symbols": 0,  # 0/null = full universe
            "scan_batch_size": 8,
            "symbols": list(DEMO_SYMBOLS),
        },
        "hybrid": {
            "weights": {"tech": 0.65, "news": 0.35},
            "enter_long": 0.45,
            "enter_short": -0.45,
            "news_veto_abs": 0.70,
        },
        "sentiment": {"vader": {"enabled": True}, "finbert": {"enabled": False}},
    }
