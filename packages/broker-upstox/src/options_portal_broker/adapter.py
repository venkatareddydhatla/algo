"""Upstox BrokerAdapter — live market data when token set; orders stay dry_run-gated.

Credentials (never commit values — env names only):
  UPSTOX_API_KEY, UPSTOX_API_SECRET, UPSTOX_ACCESS_TOKEN
  UPSTOX_REDIRECT_URI (optional; OAuth refresh path design)
  UPSTOX_REFRESH_TOKEN (optional; future token refresh)
  UPSTOX_INSTRUMENT_MAP (optional JSON symbol→instrument_key overrides)

When ``UPSTOX_ACCESS_TOKEN`` is present, ``get_candles`` calls Upstox Historical
Candle V3 and ``get_ltp`` hits market-quote/ltp. Equity dry-run paper fills
prefer LTP as the fill mark (exit/entry/soft_flatten); candle close is only a
logged fallback when LTP is unavailable. On missing token, unresolved
instrument, or HTTP failure → synthetic candles (current dry-run fallback).
Live order placement remains blocked.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Sequence
from urllib.parse import quote

import httpx

from options_portal_broker.instruments import resolve_instrument_key
from options_portal_domain.types import Candle, OrderResult, Timeframe, TradingMode

logger = logging.getLogger(__name__)

UPSTOX_API_BASE = "https://api.upstox.com"
# Historical Candle Data V3 — custom minute intervals (15m for TA).
HISTORICAL_V3_PATH = "/v3/historical-candle/{instrument_key}/{unit}/{interval}/{to_date}/{from_date}"
QUOTE_LTP_PATH = "/v2/market-quote/ltp"
TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"

_TIMEFRAME_TO_V3: dict[Timeframe, tuple[str, int]] = {
    "5m": ("minutes", 5),
    "15m": ("minutes", 15),
    "1d": ("days", 1),
}


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
        refresh_token: str | None = None,
        redirect_uri: str | None = None,
        allow_live: bool = False,
        http_client: httpx.AsyncClient | None = None,
        instrument_overrides: dict[str, str] | None = None,
        base_url: str | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("UPSTOX_API_KEY", "")
        self.api_secret = (
            api_secret if api_secret is not None else os.environ.get("UPSTOX_API_SECRET", "")
        )
        self.access_token = (
            access_token
            if access_token is not None
            else os.environ.get("UPSTOX_ACCESS_TOKEN", "")
        )
        self.refresh_token = (
            refresh_token
            if refresh_token is not None
            else os.environ.get("UPSTOX_REFRESH_TOKEN", "")
        )
        self.redirect_uri = (
            redirect_uri
            if redirect_uri is not None
            else os.environ.get(
                "UPSTOX_REDIRECT_URI", "http://127.0.0.1:8000/auth/upstox/callback"
            )
        )
        # v1 lock: live placement remains blocked even if env asks for live
        self.allow_live = allow_live and os.environ.get("UPSTOX_LIVE_UNLOCK") == "I_UNDERSTAND"
        self._client = http_client
        self._owns_client = http_client is None
        self.instrument_overrides = instrument_overrides
        self.base_url = (base_url or UPSTOX_API_BASE).rstrip("/")
        # Last successful candle path: "upstox" | "synthetic"
        self.last_candle_source: str = "synthetic"
        self.last_candle_detail: str = "no request yet"
        self.last_quote_source: str = "synthetic"

    def credentials_configured(self) -> bool:
        return bool(self.access_token)

    def market_data_source(self) -> str:
        """Configured preference: upstox when token present, else synthetic."""
        return "upstox" if self.credentials_configured() else "synthetic"

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
            self._owns_client = True
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.access_token}",
        }

    async def ensure_auth(self) -> None:
        """No interactive OAuth in this PR; token from env is enough for MD.

        If ``UPSTOX_REFRESH_TOKEN`` is set and access token is empty, a future
        call to :meth:`refresh_access_token` can mint a new access token using
        ``UPSTOX_API_KEY`` / ``UPSTOX_API_SECRET`` / ``UPSTOX_REDIRECT_URI``.
        """
        if not self.api_key and not self.access_token:
            return
        if not self.access_token and self.refresh_token:
            logger.info("access token missing; refresh_token present (call refresh_access_token)")

    async def refresh_access_token(self) -> bool:
        """Exchange refresh_token for a new access_token (non-interactive).

        Returns True on success. Does not start a browser OAuth code flow.
        Designed for when ``UPSTOX_ACCESS_TOKEN`` expires but refresh is set.
        """
        if not (self.api_key and self.api_secret and self.refresh_token):
            logger.warning(
                "refresh_access_token skipped — need UPSTOX_API_KEY, "
                "UPSTOX_API_SECRET, UPSTOX_REFRESH_TOKEN"
            )
            return False
        client = await self._get_client()
        data = {
            "client_id": self.api_key,
            "client_secret": self.api_secret,
            "redirect_uri": self.redirect_uri,
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
        }
        try:
            resp = await client.post(
                TOKEN_URL,
                data=data,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            if resp.status_code >= 400:
                logger.warning("Upstox token refresh failed status=%s", resp.status_code)
                return False
            payload = resp.json()
            token = payload.get("access_token") or (payload.get("data") or {}).get("access_token")
            if not token:
                logger.warning("Upstox token refresh response missing access_token")
                return False
            self.access_token = str(token)
            new_refresh = payload.get("refresh_token") or (payload.get("data") or {}).get(
                "refresh_token"
            )
            if new_refresh:
                self.refresh_token = str(new_refresh)
            return True
        except httpx.HTTPError:
            logger.exception("Upstox token refresh HTTP error")
            return False

    async def get_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> Sequence[Candle]:
        """Live Upstox candles when access token present; else synthetic fallback."""
        await self.ensure_auth()
        if not self.access_token:
            candles = _synthetic_candles(symbol, timeframe, start, end)
            self.last_candle_source = "synthetic"
            self.last_candle_detail = "no UPSTOX_ACCESS_TOKEN — synthetic fallback"
            return candles

        instrument_key = resolve_instrument_key(symbol, overrides=self.instrument_overrides)
        if not instrument_key:
            candles = _synthetic_candles(symbol, timeframe, start, end)
            self.last_candle_source = "synthetic"
            self.last_candle_detail = f"unresolved instrument_key for {symbol} — synthetic"
            logger.warning("No Upstox instrument_key for %s; using synthetic candles", symbol)
            return candles

        try:
            live = await self._fetch_historical_candles(
                symbol, instrument_key, timeframe, start, end
            )
            if live:
                self.last_candle_source = "upstox"
                self.last_candle_detail = f"live candles ({instrument_key})"
                return live
            self.last_candle_source = "synthetic"
            self.last_candle_detail = "Upstox returned empty candles — synthetic fallback"
            logger.warning("Upstox empty candles for %s; synthetic fallback", symbol)
        except Exception:  # noqa: BLE001
            self.last_candle_source = "synthetic"
            self.last_candle_detail = "Upstox candle fetch failed — synthetic fallback"
            logger.exception("Upstox candle fetch failed for %s; synthetic fallback", symbol)

        return _synthetic_candles(symbol, timeframe, start, end)

    async def _fetch_historical_candles(
        self,
        symbol: str,
        instrument_key: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        unit, interval = _TIMEFRAME_TO_V3[timeframe]
        to_date = end.astimezone(timezone.utc).date().isoformat()
        from_date = start.astimezone(timezone.utc).date().isoformat()
        # URL-encode instrument_key so ``NSE_INDEX|Nifty 50`` is safe in path
        encoded_key = quote(instrument_key, safe="")
        path = HISTORICAL_V3_PATH.format(
            instrument_key=encoded_key,
            unit=unit,
            interval=interval,
            to_date=to_date,
            from_date=from_date,
        )
        url = f"{self.base_url}{path}"
        client = await self._get_client()
        resp = await client.get(url, headers=self._auth_headers())
        if resp.status_code == 401 and self.refresh_token:
            refreshed = await self.refresh_access_token()
            if refreshed:
                resp = await client.get(url, headers=self._auth_headers())
        resp.raise_for_status()
        payload = resp.json()
        raw_candles = (payload.get("data") or {}).get("candles") or []
        return _parse_upstox_candles(symbol, timeframe, raw_candles)

    async def get_ltp(self, symbol: str) -> dict[str, Any] | None:
        """Fetch last traded price quote when token present; None on failure."""
        if not self.access_token:
            self.last_quote_source = "synthetic"
            return None
        instrument_key = resolve_instrument_key(symbol, overrides=self.instrument_overrides)
        if not instrument_key:
            self.last_quote_source = "synthetic"
            return None
        client = await self._get_client()
        try:
            resp = await client.get(
                f"{self.base_url}{QUOTE_LTP_PATH}",
                params={"instrument_key": instrument_key},
                headers=self._auth_headers(),
            )
            resp.raise_for_status()
            payload = resp.json()
            data = payload.get("data") or {}
            # Response keyed by exchange:tradingsymbol variants
            for _key, quote in data.items():
                if isinstance(quote, dict) and "last_price" in quote:
                    self.last_quote_source = "upstox"
                    return {
                        "symbol": symbol,
                        "instrument_key": instrument_key,
                        "last_price": float(quote["last_price"]),
                        "source": "upstox",
                    }
            self.last_quote_source = "synthetic"
            return None
        except Exception:  # noqa: BLE001
            logger.exception("Upstox LTP fetch failed for %s", symbol)
            self.last_quote_source = "synthetic"
            return None

    async def get_option_chain(self, symbol: str, expiry: date) -> dict[str, Any]:
        """Synthetic ATM-centered chain for dry-run / local demos.

        Live option-chain REST wiring is out of scope for this PR; spot may use
        live LTP when token present.
        """
        spot = {"NIFTY": 24500.0, "BANKNIFTY": 52000.0, "RELIANCE": 2920.0, "TCS": 3900.0}.get(
            symbol.upper(), 1000.0
        )
        quote = await self.get_ltp(symbol)
        if quote and quote.get("last_price"):
            spot = float(quote["last_price"])
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
        chain_source = "upstox_spot+synthetic_chain" if quote else "synthetic"
        return {
            "symbol": symbol,
            "expiry": expiry.isoformat(),
            "spot": spot,
            "atm": atm,
            "strikes": strikes,
            "source": chain_source,
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
            raise LiveTradingBlockedError("Live Upstox placement not implemented yet.")

        instrument = order.get("instrument") or "equity"
        return OrderResult(
            client_order_id=client_order_id,
            status="simulated",
            mode=mode,
            broker_order_id=f"SIM-{client_order_id}",
            message=(
                f"Dry-run / demo ({instrument}): order recorded in paper ledger only"
            ),
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
        has_secret = bool(self.api_secret)
        preferred = self.market_data_source()
        # Effective last-fetch source if any candle call happened; else preference
        effective = self.last_candle_source if self.last_candle_detail != "no request yet" else preferred
        if has_token:
            status = "ok"
            detail = (
                f"market_data={effective}; token present; instrument=equity dry_run; orders gated"
                if effective == "upstox" or self.last_candle_detail == "no request yet"
                else f"market_data={effective}; instrument=equity dry_run; {self.last_candle_detail}"
            )
        elif has_key:
            status = "degraded"
            detail = "API key set but no UPSTOX_ACCESS_TOKEN — synthetic MD; instrument=equity dry_run"
        else:
            status = "mock"
            detail = "Using synthetic MD until UPSTOX_ACCESS_TOKEN configured; instrument=equity dry_run"
        return {
            "component": "upstox",
            "status": status,
            "authenticated": has_token,
            "api_key_configured": has_key,
            "api_secret_configured": has_secret,
            "redirect_uri_configured": bool(self.redirect_uri),
            "live_unlocked": self.allow_live,
            "market_data_source": effective,
            "data_source": effective,
            "preferred_data_source": preferred,
            "last_candle_detail": self.last_candle_detail,
            "instrument": "equity",
            "execution": "dry_run",
            "detail": detail,
        }


def _parse_upstox_candles(
    symbol: str,
    timeframe: Timeframe,
    raw: Sequence[Any],
) -> list[Candle]:
    """Parse Upstox candle rows ``[ts, o, h, l, c, volume, oi?]`` into domain Candles."""
    candles: list[Candle] = []
    for row in raw:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        ts_raw, o, h, l, c = row[0], row[1], row[2], row[3], row[4]
        volume = int(row[5]) if len(row) > 5 and row[5] is not None else 0
        oi = int(row[6]) if len(row) > 6 and row[6] is not None else None
        ts = _parse_ts(ts_raw)
        if ts is None:
            continue
        candles.append(
            Candle(
                symbol=symbol,
                timeframe=timeframe,
                ts=ts,
                open=float(o),
                high=float(h),
                low=float(l),
                close=float(c),
                volume=volume,
                oi=oi,
            )
        )
    # Upstox often returns newest-first; domain TA expects chronological
    candles.sort(key=lambda x: x.ts)
    return candles


def _parse_ts(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        # Upstox timestamps are typically IST (+05:30)
        dt = dt.replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))
    return dt.astimezone(timezone.utc)


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
