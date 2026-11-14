"""Upstox BrokerAdapter — live placement gated; OAuth TODO.

TODO (requires real credentials):
- OAuth authorization-code flow → access/refresh token storage (encrypted)
- Historical candle + option-chain REST calls against Upstox v2 API
- Live order placement only after admin unlock + risk gates + TRADING_MODE=live
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Sequence

from options_portal_domain.types import Candle, OrderResult, Timeframe, TradingMode


class LiveTradingBlockedError(RuntimeError):
    """Raised when live order placement is attempted in v1-safe builds."""


class UpstoxAdapter:
    name = "upstox"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        access_token: str | None = None,
        allow_live: bool = False,
    ) -> None:
        self.api_key = api_key or os.environ.get("UPSTOX_API_KEY", "")
        self.api_secret = api_secret or os.environ.get("UPSTOX_API_SECRET", "")
        self.access_token = access_token or os.environ.get("UPSTOX_ACCESS_TOKEN", "")
        # v1 lock: live placement remains blocked even if env asks for live
        self.allow_live = allow_live and os.environ.get("UPSTOX_LIVE_UNLOCK") == "I_UNDERSTAND"

    async def ensure_auth(self) -> None:
        # TODO: refresh OAuth tokens; for scaffold we only check presence when live
        if not self.api_key:
            # Dry-run / mock path does not require credentials
            return

    async def get_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> Sequence[Candle]:
        """Return synthetic candles when credentials/API unavailable.

        TODO: call Upstox historical candle API with instrument key mapping.
        """
        await self.ensure_auth()
        if not self.access_token:
            return _synthetic_candles(symbol, timeframe, start, end)
        # TODO: real HTTP fetch
        return _synthetic_candles(symbol, timeframe, start, end)

    async def get_option_chain(self, symbol: str, expiry: date) -> dict[str, Any]:
        """Synthetic ATM-centered chain for dry-run / local demos.

        TODO: Upstox option chain endpoint.
        """
        spot = {"NIFTY": 24500.0, "BANKNIFTY": 52000.0, "RELIANCE": 2920.0, "TCS": 3900.0}.get(
            symbol.upper(), 1000.0
        )
        step = {"NIFTY": 50, "BANKNIFTY": 100}.get(symbol.upper(), 20)
        atm = round(spot / step) * step
        strikes = []
        for i in range(-3, 4):
            k = atm + i * step
            strikes.append(
                {
                    "strike": k,
                    "ce": {
                        "tradingsymbol": f"{symbol}{expiry.strftime('%y%b').upper()}{int(k)}CE",
                        "oi": 80000 - abs(i) * 5000,
                        "ltp": max(5.0, 150 - abs(i) * 30),
                        "bid": 0.0,
                        "ask": 0.0,
                        "instrument_key": f"NSE_FO|{symbol}|CE|{k}",
                    },
                    "pe": {
                        "tradingsymbol": f"{symbol}{expiry.strftime('%y%b').upper()}{int(k)}PE",
                        "oi": 75000 - abs(i) * 4000,
                        "ltp": max(5.0, 140 - abs(i) * 28),
                        "bid": 0.0,
                        "ask": 0.0,
                        "instrument_key": f"NSE_FO|{symbol}|PE|{k}",
                    },
                }
            )
        return {
            "symbol": symbol,
            "expiry": expiry.isoformat(),
            "spot": spot,
            "atm": atm,
            "strikes": strikes,
            "source": "synthetic" if not self.access_token else "upstox",
        }

    async def place_order(self, order: dict[str, Any]) -> OrderResult:
        mode: TradingMode = order.get("mode", "dry_run")  # type: ignore[assignment]
        client_order_id = order.get("client_order_id") or f"dry-{uuid.uuid4().hex[:12]}"

        if mode == "live":
            if not self.allow_live:
                raise LiveTradingBlockedError(
                    "Live order placement is blocked in v1. "
                    "Use dry_run/demo. Unlock requires UPSTOX_LIVE_UNLOCK + allow_live."
                )
            # TODO: POST /order/place with real credentials
            raise LiveTradingBlockedError("Live Upstox placement not implemented yet.")

        return OrderResult(
            client_order_id=client_order_id,
            status="simulated",
            mode=mode,
            broker_order_id=f"SIM-{client_order_id}",
            message="Dry-run / demo: order recorded in paper ledger only",
        )

    async def cancel_order(self, broker_order_id: str) -> OrderResult:
        return OrderResult(
            client_order_id=broker_order_id,
            status="cancelled",
            mode="dry_run",
            broker_order_id=broker_order_id,
            message="Cancel stub (dry-run)",
        )

    async def health(self) -> dict[str, Any]:
        has_key = bool(self.api_key)
        has_token = bool(self.access_token)
        status = "ok" if has_token else ("degraded" if has_key else "mock")
        return {
            "component": "upstox",
            "status": status,
            "authenticated": has_token,
            "api_key_configured": has_key,
            "live_unlocked": self.allow_live,
            "detail": "Using synthetic MD until OAuth tokens configured"
            if not has_token
            else "Token present (live still gated)",
        }


def _synthetic_candles(
    symbol: str,
    timeframe: Timeframe,
    start: datetime,
    end: datetime,
) -> list[Candle]:
    base = {"NIFTY": 24500.0, "BANKNIFTY": 52000.0, "RELIANCE": 2920.0, "TCS": 3900.0}.get(
        symbol.upper(), 1000.0
    )
    step_minutes = {"5m": 5, "15m": 15, "1d": 24 * 60}[timeframe]
    candles: list[Candle] = []
    ts = start.astimezone(timezone.utc)
    end_utc = end.astimezone(timezone.utc)
    i = 0
    price = base
    while ts <= end_utc and i < 80:
        drift = ((i % 7) - 3) * 0.05
        o = price
        c = price * (1 + drift / 100)
        h = max(o, c) * 1.001
        l = min(o, c) * 0.999
        candles.append(
            Candle(
                symbol=symbol,
                timeframe=timeframe,
                ts=ts,
                open=round(o, 2),
                high=round(h, 2),
                low=round(l, 2),
                close=round(c, 2),
                volume=100000 + i * 1000,
                oi=None,
            )
        )
        price = c
        ts += timedelta(minutes=step_minutes)
        i += 1
    return candles
