"""Active-watch delete: custom remove + curated exclusion + pine.signal clear."""

from __future__ import annotations

from pathlib import Path

import pytest

from options_portal_domain.config import load_config, scan_symbols, watchlist_symbols
from options_portal_domain.custom_watchlist import (
    KV_KEY_CUSTOM_SYMBOLS,
    KV_KEY_EXCLUDED_CURATED,
    add_custom_symbol,
    exclude_curated_symbol,
    filter_excluded_symbols,
    load_custom_symbols,
    load_excluded_curated_symbols,
    remove_custom_symbol,
    remove_from_active_watch,
    restore_excluded_curated_symbol,
    save_custom_symbols,
    save_excluded_curated_symbols,
)


@pytest.fixture()
def kv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import options_portal_persistence.kv_store as kv_mod

    db = tmp_path / "active-delete.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    store = kv_mod.KvStore(url=f"sqlite:///{db}")
    assert store.available
    return store


def test_filter_excluded_preserves_order():
    assert filter_excluded_symbols(
        ["A", "B", "C", "D"],
        ["c", "A"],
    ) == ["B", "D"]


def test_remove_custom_clears_pine_signal(kv):
    save_custom_symbols(["INFY"], kv=kv)
    kv.set("pine.signal.INFY", {"action": "BUY", "symbol": "INFY"})
    res = remove_custom_symbol("INFY", kv=kv)
    assert res["ok"] is True
    assert res["status"] == "removed"
    assert res.get("pine_signal_kv_cleared") is True
    assert load_custom_symbols(kv=kv) == []
    assert kv.get("pine.signal.INFY") is None


def test_exclude_curated_persists_and_clears_pine(kv):
    base = ["RELIANCE", "TCS", "INFY"]
    kv.set("pine.signal.TCS", {"action": "SELL", "symbol": "TCS"})
    res = exclude_curated_symbol("TCS", base_watchlist=base, kv=kv)
    assert res["ok"] is True
    assert res["status"] == "excluded"
    assert res.get("source") == "curated"
    assert load_excluded_curated_symbols(kv=kv) == ["TCS"]
    assert kv.get("pine.signal.TCS") is None

    again = exclude_curated_symbol("TCS", base_watchlist=base, kv=kv)
    assert again["status"] == "already_excluded"

    bad = exclude_curated_symbol("NOTBASE", base_watchlist=base, kv=kv)
    assert bad["ok"] is False
    assert bad["status"] == "not_curated"


def test_restore_excluded_and_add_custom_restores(kv):
    base = ["RELIANCE", "TCS"]
    save_excluded_curated_symbols(["TCS"], kv=kv)
    restored = restore_excluded_curated_symbol("TCS", kv=kv)
    assert restored["ok"] is True
    assert restored["status"] == "restored"
    assert load_excluded_curated_symbols(kv=kv) == []

    save_excluded_curated_symbols(["RELIANCE"], kv=kv)
    via_add = add_custom_symbol(
        "RELIANCE",
        instrument_key="NSE_EQ|INE002A01018",
        base_watchlist=base,
        kv=kv,
    )
    assert via_add["ok"] is True
    assert via_add["status"] == "restored"
    assert load_excluded_curated_symbols(kv=kv) == []


def test_remove_from_active_watch_routes_custom_vs_curated(kv):
    base = ["RELIANCE", "TCS"]
    save_custom_symbols(["INFY"], kv=kv)
    kv.set("pine.signal.INFY", {"action": "BUY"})
    kv.set("pine.signal.RELIANCE", {"action": "SELL"})

    custom = remove_from_active_watch("INFY", base_watchlist=base, kv=kv)
    assert custom["status"] == "removed"
    assert custom.get("source") == "custom"
    assert kv.get("pine.signal.INFY") is None

    curated = remove_from_active_watch("RELIANCE", base_watchlist=base, kv=kv)
    assert curated["status"] == "excluded"
    assert curated.get("source") == "curated"
    assert kv.get("pine.signal.RELIANCE") is None
    assert "RELIANCE" in load_excluded_curated_symbols(kv=kv)

    missing = remove_from_active_watch("ZZZ", base_watchlist=base, kv=kv)
    assert missing["ok"] is False
    assert missing["status"] == "not_on_watch"


def test_load_config_applies_exclusions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kv):
    save_custom_symbols(["INFY"], kv=kv)
    # Exclude a name that exists on curated TradingView CSV when present.
    cfg_probe = load_config("config/local.yaml")
    base = list(cfg_probe["trading"].get("symbols_base") or [])
    assert base, "expected curated base from config/local.yaml"
    target = base[0]
    save_excluded_curated_symbols([target], kv=kv)

    cfg = load_config("config/local.yaml")
    watch = watchlist_symbols(cfg)
    active = scan_symbols(cfg)
    assert target not in watch
    assert target not in active
    assert "INFY" in watch
    assert target in (cfg["trading"].get("symbols_excluded") or [])
    assert target not in (cfg["trading"].get("symbols_base_active") or [])
    assert kv.get(KV_KEY_EXCLUDED_CURATED) == [target]
    assert kv.get(KV_KEY_CUSTOM_SYMBOLS) == ["INFY"]


def test_api_active_delete_and_restore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    db = tmp_path / "api-active-delete.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setenv("PERSISTENCE_PROFILE", "local")

    from app.main import app

    client = TestClient(app)

    added = client.post("/api/v1/watchlist/custom", json={"symbol": "INFY"})
    assert added.status_code == 200

    # Seed pine KV via persistence used by API
    import options_portal_persistence.kv_store as kv_mod

    store = kv_mod.KvStore(url=f"sqlite:///{db}")
    store.set("pine.signal.INFY", {"action": "BUY", "symbol": "INFY"})

    gone = client.delete("/api/v1/watchlist/active/INFY?confirm=true")
    assert gone.status_code == 200
    body = gone.json()
    assert body["ok"] is True
    assert body["status"] == "removed"
    assert body.get("pine_signal_kv_cleared") is True
    assert store.get("pine.signal.INFY") is None

    no_confirm = client.delete("/api/v1/watchlist/active/INFY?confirm=false")
    assert no_confirm.status_code == 400

    # Exclude a curated name from the live universe
    wl = client.get("/api/v1/watchlist?limit=5")
    assert wl.status_code == 200
    curated_sym = None
    for row in wl.json().get("items") or []:
        if row.get("source") == "curated":
            curated_sym = row["symbol"]
            break
    assert curated_sym

    store.set(f"pine.signal.{curated_sym}", {"action": "SELL", "symbol": curated_sym})
    excl = client.delete(f"/api/v1/watchlist/active/{curated_sym}?confirm=true")
    assert excl.status_code == 200
    assert excl.json()["status"] == "excluded"
    assert store.get(f"pine.signal.{curated_sym}") is None

    listed = client.get("/api/v1/watchlist/exclusions")
    assert listed.status_code == 200
    assert any(r["symbol"] == curated_sym for r in listed.json()["items"])

    restored = client.delete(f"/api/v1/watchlist/exclusions/{curated_sym}")
    assert restored.status_code == 200
    assert restored.json()["status"] == "restored"

    listed2 = client.get("/api/v1/watchlist/exclusions")
    assert not any(r["symbol"] == curated_sym for r in listed2.json()["items"])
