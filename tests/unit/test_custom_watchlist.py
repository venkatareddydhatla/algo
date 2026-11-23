"""Custom equity symbols on top of curated TradingView watchlist."""

from __future__ import annotations

from pathlib import Path

import pytest

from options_portal_domain.config import load_config, scan_symbols, watchlist_symbols
from options_portal_domain.custom_watchlist import (
    KV_KEY_CUSTOM_SYMBOLS,
    add_custom_symbol,
    load_custom_symbols,
    merge_watchlist_with_customs,
    remove_custom_symbol,
    save_custom_symbols,
    validate_custom_equity_candidate,
)


@pytest.fixture()
def kv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import options_portal_persistence.kv_store as kv_mod

    db = tmp_path / "custom-wl.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    store = kv_mod.KvStore(url=f"sqlite:///{db}")
    assert store.available
    return store


def test_merge_dedupes_preserving_base_order():
    merged = merge_watchlist_with_customs(
        ["RELIANCE", "TCS"],
        ["INFY", "reliance", "HDFCBANK"],
    )
    assert merged == ["RELIANCE", "TCS", "INFY", "HDFCBANK"]


def test_validate_rejects_index_and_unknown():
    assert validate_custom_equity_candidate("NIFTY", instrument_key="NSE_INDEX|Nifty 50")
    assert validate_custom_equity_candidate("BANKNIFTY", instrument_key=None)
    assert "unknown" in (
        validate_custom_equity_candidate("NOTAREALSYM", instrument_key=None) or ""
    ).lower()
    assert (
        validate_custom_equity_candidate(
            "INFY", instrument_key="NSE_EQ|INE009A01021"
        )
        is None
    )


def test_add_remove_persist_roundtrip(kv):
    base = ["RELIANCE", "TCS"]
    # Pre-seed hybrid-era HOLD so Add must clear pine.signal.INFY.
    kv.set(
        "pine.signal.INFY",
        {
            "symbol": "INFY",
            "action": "HOLD",
            "reason_kind": "hybrid",
            "updated_at": "2026-11-20T10:15:00+00:00",
            "score": 0.05,
        },
    )
    added = add_custom_symbol(
        "infy",
        instrument_key="NSE_EQ|INE009A01021",
        base_watchlist=base,
        kv=kv,
    )
    assert added["ok"] is True
    assert added["status"] == "added"
    assert load_custom_symbols(kv=kv) == ["INFY"]
    assert added.get("stale_pine_kv_cleared") is True
    assert kv.get("pine.signal.INFY") is None

    dup = add_custom_symbol(
        "INFY",
        instrument_key="NSE_EQ|INE009A01021",
        base_watchlist=base,
        kv=kv,
    )
    assert dup["status"] == "already_custom"

    curated = add_custom_symbol(
        "RELIANCE",
        instrument_key="NSE_EQ|INE002A01018",
        base_watchlist=base,
        kv=kv,
    )
    assert curated["status"] == "already_on_curated"
    assert load_custom_symbols(kv=kv) == ["INFY"]

    idx = add_custom_symbol(
        "NIFTY",
        instrument_key="NSE_INDEX|Nifty 50",
        base_watchlist=base,
        kv=kv,
    )
    assert idx["ok"] is False
    assert idx["status"] == "error"

    removed = remove_custom_symbol("INFY", kv=kv)
    assert removed["ok"] is True
    assert load_custom_symbols(kv=kv) == []


def test_load_config_merges_customs_into_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kv):
    save_custom_symbols(["INFY", "HDFCBANK", "BHARTIARTL"], kv=kv)
    # BHARTIARTL is on curated TradingView list — merge must not double it.
    monkeypatch.setenv("CONFIG_PATH", str(Path("config/local.yaml").resolve()))
    # Force KvStore() default path to our temp DB via DATABASE_URL already set.
    cfg = load_config("config/local.yaml")
    # Re-inject via store used by load_custom_symbols (DATABASE_URL)
    assert kv.get(KV_KEY_CUSTOM_SYMBOLS) == ["INFY", "HDFCBANK", "BHARTIARTL"]

    watch = watchlist_symbols(cfg)
    active = scan_symbols(cfg)
    assert watch == active
    assert watch.count("BHARTIARTL") == 1
    assert "INFY" in watch
    assert "HDFCBANK" in watch
    # Customs that are not on curated appear in symbols_custom
    customs = cfg["trading"].get("symbols_custom") or []
    assert "INFY" in customs
    assert "HDFCBANK" in customs
    assert "BHARTIARTL" not in customs  # already on base


def test_remove_curated_name_fails(kv):
    save_custom_symbols(["INFY"], kv=kv)
    res = remove_custom_symbol("TCS", kv=kv)
    assert res["ok"] is False
    assert res["status"] == "not_custom"


def test_api_add_reject_index_and_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    db = tmp_path / "api-custom.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setenv("PERSISTENCE_PROFILE", "local")

    from app.main import app

    client = TestClient(app)

    bad = client.post("/api/v1/watchlist/custom", json={"symbol": "NIFTY"})
    assert bad.status_code == 400
    assert "index" in str(bad.json().get("detail", "")).lower()

    unknown = client.post("/api/v1/watchlist/custom", json={"symbol": "ZZZNOPE123"})
    assert unknown.status_code == 400
    assert "unknown" in str(unknown.json().get("detail", "")).lower()

    ok = client.post("/api/v1/watchlist/custom", json={"symbol": "INFY"})
    assert ok.status_code == 200
    body = ok.json()
    assert body["ok"] is True
    assert body["status"] == "added"
    assert "INFY" in (body.get("custom_symbols") or body.get("customs") or [])

    listed = client.get("/api/v1/watchlist/custom")
    assert listed.status_code == 200
    assert any(r["symbol"] == "INFY" for r in listed.json()["items"])

    # Already on curated TradingView list
    curated = client.post("/api/v1/watchlist/custom", json={"symbol": "BHARTIARTL"})
    assert curated.status_code == 200
    assert curated.json()["status"] == "already_on_curated"

    gone = client.delete("/api/v1/watchlist/custom/INFY")
    assert gone.status_code == 200
    assert gone.json()["status"] == "removed"
