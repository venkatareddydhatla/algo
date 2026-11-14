"""NSE F&O universe loader + config resolution."""

from __future__ import annotations

from pathlib import Path

from options_portal_domain.config import load_config, scan_symbols, watchlist_symbols
from options_portal_domain.universe import (
    DEMO_SYMBOLS,
    active_scan_symbols,
    load_universe_csv,
    resolve_watchlist_symbols,
)

ROOT = Path(__file__).resolve().parents[2]
CSV = ROOT / "config" / "universe" / "nse_fno_underlyings.csv"


def test_universe_csv_has_indices_and_equities():
    rows = load_universe_csv(CSV)
    symbols = {r.symbol for r in rows}
    assert len(rows) >= 200
    for sym in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "RELIANCE", "TCS"):
        assert sym in symbols
    assert any(r.kind == "index" for r in rows)
    assert any(r.kind == "equity" for r in rows)


def test_resolve_universe_alias():
    symbols = resolve_watchlist_symbols({"universe": "nse_fno"}, config_dir=ROOT / "config")
    assert "NIFTY" in symbols
    assert "RELIANCE" in symbols
    assert len(symbols) >= 200


def test_symbols_demo_flag():
    symbols = resolve_watchlist_symbols(
        {
            "universe": "nse_fno",
            "symbols_demo": list(DEMO_SYMBOLS),
            "use_symbols_demo": True,
        },
        config_dir=ROOT / "config",
    )
    assert symbols == list(DEMO_SYMBOLS)


def test_active_scan_cap():
    watch = resolve_watchlist_symbols({"universe": "nse_fno"}, config_dir=ROOT / "config")
    active = active_scan_symbols(watch, {"max_active_symbols": 20})
    assert len(active) == 20
    assert active == watch[:20]


def test_load_config_resolves_local_yaml(monkeypatch):
    monkeypatch.chdir(ROOT)
    monkeypatch.setenv("CONFIG_PATH", str(ROOT / "config" / "local.yaml"))
    cfg = load_config()
    wl = watchlist_symbols(cfg)
    active = scan_symbols(cfg)
    assert len(wl) >= 200
    assert len(active) == int(cfg["trading"].get("max_active_symbols", 20))
    assert set(active).issubset(set(wl))
