"""Replaceable plugin protocols (stable contracts)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Protocol, Sequence

from options_portal_domain.types import (
    Candle,
    HybridDecision,
    NewsItem,
    OptionsRecommendation,
    OrderResult,
    SentimentResult,
    TechnicalSignal,
    Timeframe,
    TradingMode,
)


class BrokerAdapter(Protocol):
    name: str

    async def ensure_auth(self) -> None: ...

    async def get_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> Sequence[Candle]: ...

    async def get_option_chain(self, symbol: str, expiry: date) -> dict[str, Any]: ...

    async def place_order(self, order: dict[str, Any]) -> OrderResult: ...

    async def cancel_order(self, broker_order_id: str) -> OrderResult: ...

    async def health(self) -> dict[str, Any]: ...


class IndicatorPlugin(Protocol):
    name: str
    version: str

    def compute(self, candles: Sequence[Candle]) -> dict[str, float | None]: ...


class SignalGenerator(Protocol):
    name: str

    def evaluate(
        self,
        symbol: str,
        candles: Sequence[Candle],
        features: dict[str, Any],
    ) -> TechnicalSignal: ...


class NewsIngestor(Protocol):
    source: str

    async def fetch_since(self, since: datetime) -> Sequence[NewsItem]: ...


class SentimentScorer(Protocol):
    model: str

    async def score_batch(
        self,
        items: Sequence[NewsItem],
    ) -> Sequence[SentimentResult]: ...


class HybridScorer(Protocol):
    def combine(
        self,
        tech: TechnicalSignal,
        sentiments: Sequence[SentimentResult],
        news: Sequence[NewsItem],
        context: dict[str, Any],
    ) -> HybridDecision: ...


class OptionsRecommender(Protocol):
    def recommend(
        self,
        decision: HybridDecision,
        chain: dict[str, Any],
        risk_profile: dict[str, Any],
    ) -> OptionsRecommendation | None: ...


class RiskGate(Protocol):
    def validate(
        self,
        reco: OptionsRecommendation,
        portfolio: dict[str, Any],
        limits: dict[str, Any],
    ) -> tuple[bool, str]: ...


class OrderRouter(Protocol):
    async def execute(
        self,
        reco: OptionsRecommendation,
        mode: TradingMode,
        actor_user_id: str | None,
    ) -> OrderResult: ...


class Notifier(Protocol):
    async def send(self, title: str, body: str, meta: dict[str, Any]) -> None: ...


class MarketCalendar(Protocol):
    def is_trading_day(self, d: date) -> bool: ...

    def session_open_close_ist(self, d: date) -> tuple[datetime, datetime]: ...

    def next_weekly_expiry(self, symbol: str, as_of: date) -> date: ...

    def next_monthly_expiry(self, symbol: str, as_of: date) -> date: ...
