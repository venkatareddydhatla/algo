"""Pipeline equity mark guards — per-symbol LTP / refuse synthetic under live token."""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from options_portal_domain.types import Candle, SignalAction


def _load_worker_pipeline(*, module_name: str = "worker_pipeline_under_test"):
    """Load apps/worker/app/pipeline.py without colliding with apps/api/app."""
    path = Path(__file__).resolve().parents[2] / "apps" / "worker" / "app" / "pipeline.py"
    # Always reload so monkeypatches from a prior test cannot leak
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _candles(symbol: str, close: float) -> list[Candle]:
    ts = datetime(2026, 11, 16, 6, 0, tzinfo=timezone.utc)
    return [
        Candle(
            symbol=symbol,
            timeframe="15m",
            ts=ts + timedelta(minutes=15 * i),
            open=close,
            high=close * 1.001,
            low=close * 0.999,
            close=close,
            volume=1000,
        )
        for i in range(40)
    ]


@pytest.mark.asyncio
async def test_pipeline_ioc_entry_uses_ioc_ltp_not_360one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """IOC entry mark must be IOC LTP (144.04), never 360ONE 996.96."""
    db = tmp_path / "pipeline-ioc.sqlite"
    monkeypatch.setenv("PERSISTENCE_PROFILE", "local")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    # Unique module name so sibling tests cannot share patched globals
    pipeline_mod = _load_worker_pipeline(module_name="worker_pipeline_ioc_ltp")

    class FakeBroker:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.instrument_overrides = None
            self.last_candle_source = "upstox"
            self.last_candle_detail = "live"
            self.last_quote_source = "upstox"
            self._prices = {"IOC": 144.04, "360ONE": 996.96}

        def credentials_configured(self) -> bool:
            return True

        async def get_candles(self, symbol: str, *args: Any, **kwargs: Any):
            self.last_candle_source = "upstox"
            px = self._prices[symbol.upper()]
            return _candles(symbol, px)

        async def get_ltp(self, symbol: str):
            from options_portal_broker.instruments import resolve_instrument_key

            sym = symbol.upper()
            return {
                "symbol": sym,
                "instrument_key": resolve_instrument_key(sym),
                "last_price": self._prices[sym],
                "source": "upstox",
            }

        async def place_order(self, order: dict[str, Any]):
            from options_portal_domain.types import OrderResult

            return OrderResult(
                client_order_id=order.get("client_order_id") or "x",
                status="simulated",
                mode="dry_run",
                broker_order_id="SIM",
                message="ok",
            )

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(pipeline_mod, "UpstoxAdapter", FakeBroker)
    monkeypatch.setattr(pipeline_mod, "is_nse_trading_day", lambda _d: True)
    # Keep soft-EOD / no-new-entries from blocking based on wall-clock IST
    monkeypatch.setattr(
        pipeline_mod.SoftEodPolicy,
        "past_no_new_entries",
        lambda self, now=None: False,
    )
    monkeypatch.setattr(
        pipeline_mod.SoftEodPolicy,
        "past_soft_flatten",
        lambda self, now=None: False,
    )

    tech = MagicMock()
    tech.action = SignalAction.BUY_PE_BIAS
    tech.score = -0.6
    monkeypatch.setattr(
        pipeline_mod.ConfluenceSignalGenerator,
        "evaluate",
        lambda self, symbol, candles, features: tech,
    )

    decision = MagicMock()
    decision.action = SignalAction.BUY_PE_BIAS
    decision.hybrid_score = -0.55
    decision.tech_score = -0.6
    decision.news_score = 0.0
    decision.confidence = 0.7
    decision.reasons = ["test"]
    decision.reason_kind = "technical"
    decision.reason_summary = "test PE"
    decision.reason_detail = "test"
    monkeypatch.setattr(
        pipeline_mod.WeightedHybridScorer,
        "combine",
        lambda self, *a, **k: decision,
    )
    monkeypatch.setattr(pipeline_mod, "build_notifier", lambda cfg: AsyncMock())
    monkeypatch.setattr(pipeline_mod, "build_gmail_notifier", lambda cfg: None)

    cfg = {
        "trading": {
            "mode": "dry_run",
            "halt": False,
            "equity": {"enabled": True, "share_qty": 1},
            "risk": {"max_open_symbols": 10},
        },
        "hybrid": {"weights": {"tech": 0.65, "news": 0.35}},
        "broker": {"candle_history_days": 5},
        "session": {},
        "market_sentiment": {"enabled": False},
    }

    result = await pipeline_mod.run_symbol_tick("IOC", cfg)
    assert result["mark_source"] == "ltp"
    assert result["mark_price"] == pytest.approx(144.04)
    assert result["instrument_key"] == "NSE_EQ|INE242A01010"
    assert result["mark_price"] != pytest.approx(996.96)
    orders = result.get("orders") or []
    assert orders, result.get("note") or result
    assert orders[0]["price"] == pytest.approx(144.04)


@pytest.mark.asyncio
async def test_pipeline_refuses_synthetic_mark_when_token_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """With UPSTOX token, unresolved key → no paper fill (no shared synthetic print)."""
    db = tmp_path / "pipeline-refuse.sqlite"
    monkeypatch.setenv("PERSISTENCE_PROFILE", "local")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    pipeline_mod = _load_worker_pipeline(module_name="worker_pipeline_refuse_synth")

    class FakeBroker:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.instrument_overrides = None
            self.last_candle_source = "synthetic"
            self.last_candle_detail = "unresolved"
            self.last_quote_source = "synthetic"

        def credentials_configured(self) -> bool:
            return True

        async def get_candles(self, symbol: str, *args: Any, **kwargs: Any):
            # Distinct synthetic close that previously looked "plausible"
            return _candles(symbol, 3958.95)

        async def get_ltp(self, symbol: str):
            return None

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(pipeline_mod, "UpstoxAdapter", FakeBroker)
    monkeypatch.setattr(pipeline_mod, "is_nse_trading_day", lambda _d: True)
    monkeypatch.setattr(
        pipeline_mod,
        "resolve_instrument_key",
        lambda symbol, overrides=None: None,
    )

    tech = MagicMock()
    tech.action = SignalAction.BUY_PE_BIAS
    monkeypatch.setattr(
        pipeline_mod.ConfluenceSignalGenerator,
        "evaluate",
        lambda self, symbol, candles, features: tech,
    )
    decision = MagicMock()
    decision.action = SignalAction.BUY_PE_BIAS
    decision.hybrid_score = -0.55
    decision.tech_score = -0.6
    decision.news_score = 0.0
    decision.confidence = 0.7
    decision.reasons = ["test"]
    decision.reason_kind = "technical"
    decision.reason_summary = "test"
    decision.reason_detail = "test"
    monkeypatch.setattr(
        pipeline_mod.WeightedHybridScorer,
        "combine",
        lambda self, *a, **k: decision,
    )
    monkeypatch.setattr(pipeline_mod, "build_notifier", lambda cfg: AsyncMock())
    monkeypatch.setattr(pipeline_mod, "build_gmail_notifier", lambda cfg: None)

    cfg = {
        "trading": {
            "mode": "dry_run",
            "halt": False,
            "equity": {"enabled": True, "share_qty": 1},
            "risk": {},
        },
        "hybrid": {"weights": {"tech": 0.65, "news": 0.35}},
        "broker": {"candle_history_days": 5},
        "market_sentiment": {"enabled": False},
    }
    result = await pipeline_mod.run_symbol_tick("IOC", cfg)
    assert result.get("order") is None
    assert "refusing paper fill" in str(result.get("note") or "")
    assert result.get("orders") in (None, [])
