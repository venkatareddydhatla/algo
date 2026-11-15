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
        return httpx.Response(200, json=_ltp_payload())

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = UpstoxAdapter(access_token="test-token-not-real", http_client=client)
        quote = await adapter.get_ltp("NIFTY")

    assert quote is not None
    assert quote["last_price"] == 24555.5
    assert quote["source"] == "upstox"


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
