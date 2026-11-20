"""NSE F&O universe loader + config resolution."""

from __future__ import annotations

from pathlib import Path

from options_portal_domain.config import load_config, scan_symbols, watchlist_symbols
from options_portal_domain.universe import (
    DEMO_SYMBOLS,
    INDEX_SYMBOLS,
    active_scan_symbols,
    instrument_key_is_index,
    is_index_underlying,
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


def test_is_index_underlying_denylist_and_instrument_key():
    """Equity book must refuse known indices + NSE_INDEX/BSE_INDEX keys."""
    for sym in (
        "NIFTY",
        "BANKNIFTY",
        "FINNIFTY",
        "MIDCPNIFTY",
        "SENSEX",
        "NIFTYNXT50",
        "INDIAVIX",
    ):
        assert is_index_underlying(sym) is True
        assert sym in INDEX_SYMBOLS or is_index_underlying(sym)

    assert instrument_key_is_index("NSE_INDEX|Nifty 50") is True
    assert instrument_key_is_index("BSE_INDEX|SENSEX") is True
    assert instrument_key_is_index("NSE_INDEX:Nifty Bank") is True
    assert instrument_key_is_index("NSE_EQ|INE002A01018") is False

    assert is_index_underlying("RELIANCE", instrument_key="NSE_EQ|INE002A01018") is False
    assert is_index_underlying("TCS") is False
    # kind / key win even for unknown symbol names
    assert is_index_underlying("CUSTOMIDX", kind="index") is True
    assert is_index_underlying(
        "WEIRD", instrument_key="NSE_INDEX|Something"
    ) is True
    # CSV index rows
    rows = {r.symbol: r for r in load_universe_csv(CSV)}
    assert is_index_underlying("NIFTY", kind=rows["NIFTY"].kind) is True
    assert is_index_underlying("RELIANCE", kind=rows["RELIANCE"].kind) is False


def test_resolve_universe_alias():
    symbols = resolve_watchlist_symbols({"universe": "nse_fno"}, config_dir=ROOT / "config")
    assert "NIFTY" in symbols
    assert "RELIANCE" in symbols
    assert len(symbols) >= 200


def test_resolve_volatile_fno50_universe():
    """Default trading universe is Venkata’s volatile equities (not full F&O)."""
    csv_path = ROOT / "config" / "universe" / "volatile_fno50_underlyings.csv"
    rows = load_universe_csv(csv_path)
    symbols = resolve_watchlist_symbols(
        {"universe": "volatile_fno50"}, config_dir=ROOT / "config"
    )
    assert len(symbols) == len(rows) == 49
    assert len(symbols) == len(set(symbols))  # PIIND once
    assert "TMPV" in symbols  # Tata Motors → TMPV
    assert "LTM" in symbols  # LTIMindtree LTIM → LTM
    assert "GUJENERGY" in symbols  # Gujarat Gas → GUJENERGY
    assert "PIIND" in symbols
    assert "BHEL" in symbols and "HAL" in symbols
    assert "SYNGENE" in symbols and "MPHASIS" in symbols
    # Dropped sub-₹40 / removed names
    assert "IDEA" not in symbols
    assert "ASAHIINDIA" not in symbols
    assert "INDIACEM" not in symbols
    assert "DELTACORP" not in symbols
    assert "FINNIFTY" not in symbols
    assert "IDFC" not in symbols
    assert "NIFTY" not in symbols
    assert all(r.kind == "equity" for r in rows)


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


def test_active_scan_cap_positive():
    watch = resolve_watchlist_symbols(
        {"universe": "volatile_fno50"}, config_dir=ROOT / "config"
    )
    active = active_scan_symbols(watch, {"max_active_symbols": 20})
    assert len(active) == 20
    assert active == watch[:20]


def test_active_scan_unlimited_zero_or_null():
    watch = resolve_watchlist_symbols(
        {"universe": "volatile_fno50"}, config_dir=ROOT / "config"
    )
    assert active_scan_symbols(watch, {"max_active_symbols": 0}) == watch
    assert active_scan_symbols(watch, {"max_active_symbols": None}) == watch
    assert len(active_scan_symbols(watch, {})) == len(watch)


def test_load_config_resolves_local_yaml(monkeypatch):
    monkeypatch.chdir(ROOT)
    monkeypatch.setenv("CONFIG_PATH", str(ROOT / "config" / "local.yaml"))
    cfg = load_config()
    wl = watchlist_symbols(cfg)
    active = scan_symbols(cfg)
    assert cfg["trading"].get("universe") == "volatile_fno50"
    assert cfg["trading"]["risk"].get("min_equity_price") == 40.0
    assert len(wl) == 49
    assert "PIIND" in wl
    assert "IDEA" not in wl
    assert "FINNIFTY" not in wl
    # Default: score entire configured universe (max_active_symbols 0/null)
    max_active = cfg["trading"].get("max_active_symbols", 0)
    if max_active is None or int(max_active) <= 0:
        assert active == wl
    else:
        assert len(active) == int(max_active)
        assert set(active).issubset(set(wl))
