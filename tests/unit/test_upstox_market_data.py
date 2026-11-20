"""Upstox live candle/quote wiring — mocked HTTP only (no real credentials)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from options_portal_broker.adapter import LiveTradingBlockedError, UpstoxAdapter
from options_portal_broker.instruments import resolve_instrument_key


def _candle_payload() -> dict:
    return {
        "status": "success",
        "data": {
            "candles": [
                ["2024-06-03T09:15:00+05:30", 24500.0, 24550.0, 24480.0, 24520.0, 1000, 0],
                ["2024-06-03T09:30:00+05:30", 24520.0, 24580.0, 24510.0, 24560.0, 1100, 0],
                ["2024-06-03T09:45:00+05:30", 24560.0, 24600.0, 24540.0, 24590.0, 1200, 0],
            ]
        },
    }


def _ltp_payload() -> dict:
    return {
        "status": "success",
        "data": {
            "NSE_INDEX:Nifty 50": {
                "last_price": 24555.5,
                "instrument_token": "NSE_INDEX|Nifty 50",
            }
        },
    }


@pytest.mark.asyncio
async def test_synthetic_when_no_token():
    adapter = UpstoxAdapter(access_token="", api_key="", api_secret="")
    end = datetime(2024, 6, 3, 12, tzinfo=timezone.utc)
    start = end - timedelta(days=1)
    candles = await adapter.get_candles("NIFTY", "15m", start, end)
    assert candles
    assert adapter.last_candle_source == "synthetic"
    health = await adapter.health()
    assert health["market_data_source"] == "synthetic"
    assert health["data_source"] == "synthetic"
    assert health["status"] == "mock"


@pytest.mark.asyncio
async def test_get_real_candles_empty_without_token():
    """Backtest path must not receive synthetic bars from get_real_candles."""
    adapter = UpstoxAdapter(access_token="", api_key="", api_secret="")
    end = datetime(2024, 6, 3, 12, tzinfo=timezone.utc)
    start = end - timedelta(days=1)
    candles = await adapter.get_real_candles("RVNL", "15m", start, end)
    assert candles == []
    assert adapter.last_candle_source == "unavailable"
    assert "token" in adapter.last_candle_detail.lower()


@pytest.mark.asyncio
async def test_live_candles_when_token_present():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "Authorization" in request.headers
        assert request.headers["Authorization"] == "Bearer test-token-not-real"
        assert "/v3/historical-candle/" in str(request.url)
        assert "minutes/15/" in str(request.url)
        return httpx.Response(200, json=_candle_payload())

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = UpstoxAdapter(
            access_token="test-token-not-real",
            api_key="test-key",
            api_secret="test-secret",
            http_client=client,
            base_url="https://api.upstox.com",
        )
        end = datetime(2024, 6, 3, 12, tzinfo=timezone.utc)
        start = end - timedelta(days=1)
        candles = await adapter.get_candles("NIFTY", "15m", start, end)

    assert len(candles) == 3
    assert candles[0].close == 24520.0
    assert candles[0].ts < candles[-1].ts  # chronological
    assert adapter.last_candle_source == "upstox"
    health = await adapter.health()
    assert health["market_data_source"] == "upstox"
    assert health["authenticated"] is True
    assert health["status"] == "ok"


@pytest.mark.asyncio
async def test_http_failure_falls_back_to_synthetic():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"status": "error"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = UpstoxAdapter(
            access_token="test-token-not-real",
            http_client=client,
        )
        end = datetime(2024, 6, 3, 12, tzinfo=timezone.utc)
        start = end - timedelta(hours=6)
        candles = await adapter.get_candles("RELIANCE", "15m", start, end)

    assert candles  # synthetic fallback
    assert adapter.last_candle_source == "synthetic"
    assert "failed" in adapter.last_candle_detail.lower() or "fallback" in adapter.last_candle_detail.lower()


@pytest.mark.asyncio
async def test_unresolved_symbol_synthetic_with_token():
    adapter = UpstoxAdapter(access_token="test-token-not-real")
    end = datetime(2024, 6, 3, 12, tzinfo=timezone.utc)
    start = end - timedelta(hours=6)
    candles = await adapter.get_candles("ZZUNKNOWNXYZ", "15m", start, end)
    assert candles
    assert adapter.last_candle_source == "synthetic"
    assert "unresolved" in adapter.last_candle_detail.lower()


@pytest.mark.asyncio
async def test_get_ltp_mocked():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "market-quote/ltp" in str(request.url)
        # V2 OpenAPI query param is ``symbol`` (instrument_key value)
        assert "symbol=" in str(request.url)
        assert "instrument_key=" not in str(request.url)
        return httpx.Response(200, json=_ltp_payload())

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = UpstoxAdapter(access_token="test-token-not-real", http_client=client)
        quote = await adapter.get_ltp("NIFTY")

    assert quote is not None
    assert quote["last_price"] == 24555.5
    assert quote["source"] == "upstox"
    assert quote["symbol"] == "NIFTY"


@pytest.mark.asyncio
async def test_get_ltp_ignores_other_symbol_first_quote():
    """Multi-quote payload must not price BPCL with 360ONE's LTP (first key)."""
    from options_portal_broker.adapter import select_ltp_from_quote_data
    from options_portal_broker.instruments import resolve_instrument_key

    payload = {
        "NSE_EQ:360ONE": {
            "last_price": 996.96,
            "instrument_token": "NSE_EQ|INE726L01019",
        },
        "NSE_EQ:BPCL": {
            "last_price": 340.55,
            "instrument_token": "NSE_EQ|INE029A01011",
        },
    }
    bpcl_key = resolve_instrument_key("BPCL")
    assert bpcl_key is not None
    assert select_ltp_from_quote_data(
        payload, symbol="BPCL", instrument_key=bpcl_key
    ) == pytest.approx(340.55)
    # First-wins would have returned 996.96 — that bug caused shared Exit px
    assert select_ltp_from_quote_data(
        payload, symbol="BPCL", instrument_key=bpcl_key
    ) != pytest.approx(996.96)

    def handler(request: httpx.Request) -> httpx.Response:
        assert "symbol=" in str(request.url)
        return httpx.Response(200, json={"status": "success", "data": payload})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = UpstoxAdapter(access_token="test-token-not-real", http_client=client)
        quote = await adapter.get_ltp("BPCL")

    assert quote is not None
    assert quote["last_price"] == pytest.approx(340.55)
    assert quote["symbol"] == "BPCL"


@pytest.mark.asyncio
async def test_get_ltp_map_per_symbol_prices():
    from urllib.parse import unquote, urlparse, parse_qs

    prices = {
        "BPCL": 340.55,
        "AXISBANK": 1120.0,
        "ASIANPAINT": 2910.25,
    }
    by_key = {resolve_instrument_key(s): (s, px) for s, px in prices.items()}

    def handler(request: httpx.Request) -> httpx.Response:
        qs = parse_qs(urlparse(str(request.url)).query)
        raw = unquote((qs.get("symbol") or [""])[0])
        assert raw in by_key
        sym, px = by_key[raw]
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    f"NSE_EQ:{sym}": {
                        "last_price": px,
                        "instrument_token": raw,
                    }
                },
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = UpstoxAdapter(access_token="test-token-not-real", http_client=client)
        marks = await adapter.get_ltp_map(list(prices))

    assert marks == prices
    assert len(set(marks.values())) == len(prices)


def test_synthetic_bases_differ_across_symbols():
    from options_portal_broker.adapter import _synthetic_base_price

    bases = {
        s: _synthetic_base_price(s)
        for s in ("360ONE", "BPCL", "AXISBANK", "ASIANPAINT", "APOLLOHOSP", "ADANIPORTS")
    }
    assert len(set(bases.values())) == len(bases)
    assert bases["360ONE"] != 1000.0 or bases["BPCL"] != bases["360ONE"]


def test_360one_and_ioc_have_correct_instrument_keys():
    """Stale/missing ISINs caused LTP miss → synthetic/shared marks (996.96 bug)."""
    from options_portal_broker.instruments import clear_instrument_key_cache

    clear_instrument_key_cache()
    # Current Upstox NSE master ISIN for 360 ONE WAM (not legacy INE726L01019)
    assert resolve_instrument_key("360ONE", overrides=None) == "NSE_EQ|INE466L01038"
    assert resolve_instrument_key("IOC", overrides=None) == "NSE_EQ|INE242A01010"
    assert resolve_instrument_key("BPCL", overrides=None) == "NSE_EQ|INE029A01011"
    # Face-value / ISIN refresh cases
    assert resolve_instrument_key("KOTAKBANK") == "NSE_EQ|INE237A01036"
    assert resolve_instrument_key("BAJFINANCE") == "NSE_EQ|INE296A01032"
    # Must not share keys across names
    assert resolve_instrument_key("IOC") != resolve_instrument_key("360ONE")


def test_fno_universe_equities_all_resolve_instrument_keys():
    import csv
    from pathlib import Path

    from options_portal_broker.instruments import clear_instrument_key_cache

    clear_instrument_key_cache()
    path = Path("config/universe/nse_fno_underlyings.csv")
    assert path.is_file()
    missing: list[str] = []
    for row in csv.DictReader(path.open(encoding="utf-8")):
        if str(row.get("kind") or "").strip().lower() != "equity":
            continue
        sym = str(row.get("symbol") or "").strip().upper()
        if resolve_instrument_key(sym) is None:
            missing.append(sym)
    assert missing == [], f"unmapped F&O equities (LTP will fail): {missing[:20]}"


def test_volatile_fno50_equities_all_resolve_instrument_keys():
    import csv
    from pathlib import Path

    from options_portal_broker.instruments import clear_instrument_key_cache

    clear_instrument_key_cache()
    path = Path("config/universe/volatile_fno50_underlyings.csv")
    assert path.is_file()
    missing: list[str] = []
    for row in csv.DictReader(path.open(encoding="utf-8")):
        sym = str(row.get("symbol") or "").strip().upper()
        if resolve_instrument_key(sym) is None:
            missing.append(sym)
    assert missing == [], f"unmapped volatile equities (LTP will fail): {missing}"
    # Legacy names resolve via aliases to current tradingsymbols
    assert resolve_instrument_key("TATAMOTORS") == resolve_instrument_key("TMPV")
    assert resolve_instrument_key("LTIM") == resolve_instrument_key("LTM")
    assert resolve_instrument_key("GUJGASLTD") == resolve_instrument_key("GUJENERGY")
    assert resolve_instrument_key("ASAHIINDIA") == "NSE_EQ|INE439A01020"


@pytest.mark.asyncio
async def test_get_ltp_ioc_ignores_360one_quote():
    """IOC must never take 360ONE's last_price from a multi-quote payload."""
    from options_portal_broker.adapter import select_ltp_from_quote_data

    ioc_key = resolve_instrument_key("IOC")
    one_key = resolve_instrument_key("360ONE")
    assert ioc_key and one_key and ioc_key != one_key
    payload = {
        "NSE_EQ:360ONE": {
            "last_price": 996.96,
            "instrument_token": one_key,
        },
        "NSE_EQ:IOC": {
            "last_price": 144.04,
            "instrument_token": ioc_key,
        },
    }
    assert select_ltp_from_quote_data(
        payload, symbol="IOC", instrument_key=ioc_key
    ) == pytest.approx(144.04)
    assert select_ltp_from_quote_data(
        payload, symbol="IOC", instrument_key=ioc_key
    ) != pytest.approx(996.96)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "success", "data": payload})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = UpstoxAdapter(access_token="test-token-not-real", http_client=client)
        quote = await adapter.get_ltp("IOC")

    assert quote is not None
    assert quote["symbol"] == "IOC"
    assert quote["instrument_key"] == ioc_key
    assert quote["last_price"] == pytest.approx(144.04)


@pytest.mark.asyncio
async def test_orders_remain_dry_run_blocked():
    adapter = UpstoxAdapter(access_token="test-token-not-real", allow_live=False)
    result = await adapter.place_order({"mode": "dry_run", "side": "BUY"})
    assert result.mode == "dry_run"
    assert result.status == "simulated"
    with pytest.raises(LiveTradingBlockedError):
        await adapter.place_order({"mode": "live"})


@pytest.mark.asyncio
async def test_refresh_token_design_mocked():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "authorization/token" in str(request.url):
            return httpx.Response(
                200,
                json={"access_token": "new-access-not-real", "refresh_token": "new-refresh"},
            )
        return httpx.Response(401, json={"status": "error"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = UpstoxAdapter(
            access_token="",
            api_key="key",
            api_secret="secret",
            refresh_token="old-refresh",
            redirect_uri="http://127.0.0.1:8000/auth/upstox/callback",
            http_client=client,
        )
        ok = await adapter.refresh_access_token()

    assert ok is True
    assert adapter.access_token == "new-access-not-real"
    assert any("authorization/token" in c for c in calls)


def test_resolve_instrument_keys():
    assert resolve_instrument_key("NIFTY") == "NSE_INDEX|Nifty 50"
    assert resolve_instrument_key("BANKNIFTY") == "NSE_INDEX|Nifty Bank"
    assert resolve_instrument_key("SENSEX") == "BSE_INDEX|SENSEX"
    assert resolve_instrument_key("RELIANCE", overrides=None).startswith("NSE_EQ|")
    assert resolve_instrument_key("nope") is None
    assert (
        resolve_instrument_key("CUSTOM", overrides={"CUSTOM": "NSE_EQ|INE000"})
        == "NSE_EQ|INE000"
    )


@pytest.mark.asyncio
async def test_health_dashboard_exposes_data_source(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PERSISTENCE_PROFILE", "local")
    monkeypatch.delenv("UPSTOX_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("UPSTOX_API_KEY", raising=False)

    from app.routes.health import health_dashboard

    payload = await health_dashboard()
    assert payload["market_data_source"] == "synthetic"
    assert payload["data_source"] == "synthetic"
    names = {c["name"] for c in payload["components"]}
    assert "market_data" in names
    assert "upstox" in names


@pytest.mark.asyncio
async def test_no_secrets_in_health_payload(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("UPSTOX_ACCESS_TOKEN", "super-secret-token-value")
    monkeypatch.setenv("UPSTOX_API_KEY", "super-secret-key-value")
    monkeypatch.setenv("UPSTOX_API_SECRET", "super-secret-secret-value")

    adapter = UpstoxAdapter()
    health = await adapter.health()
    blob = str(health)
    assert "super-secret-token-value" not in blob
    assert "super-secret-key-value" not in blob
    assert "super-secret-secret-value" not in blob
    assert health["authenticated"] is True
    assert health["api_key_configured"] is True
