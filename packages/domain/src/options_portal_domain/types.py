"""Core domain types for the options signal portal."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Literal

TradingMode = Literal["dry_run", "demo", "live"]
Timeframe = Literal["5m", "15m", "1d"]


class SignalAction(str, Enum):
    HOLD = "HOLD"
    BUY_CE_BIAS = "BUY_CE_BIAS"
    BUY_PE_BIAS = "BUY_PE_BIAS"
    EXIT = "EXIT"
    REDUCE = "REDUCE"


@dataclass(frozen=True)
class Candle:
    symbol: str
    timeframe: Timeframe
    ts: datetime  # bar open, aware UTC
    open: float
    high: float
    low: float
    close: float
    volume: int
    oi: int | None = None


@dataclass(frozen=True)
class TechnicalSignal:
    symbol: str
    timeframe: Timeframe
    bar_ts: datetime
    action: SignalAction
    score: float  # [-1, 1]
    confidence: float  # [0, 1]
    reasons: list[str]
    features: dict[str, Any]


@dataclass(frozen=True)
class NewsItem:
    id: str
    source: str
    title: str
    body: str
    published_at: datetime
    symbols: list[str]
    url: str | None = None


@dataclass(frozen=True)
class SentimentResult:
    news_id: str
    model: str
    score: float
    label: Literal["bullish", "bearish", "neutral"]
    confidence: float


@dataclass(frozen=True)
class HybridDecision:
    symbol: str
    action: SignalAction
    tech_score: float
    news_score: float
    hybrid_score: float
    confidence: float
    reasons: list[str]
    news_ids: list[str]
    bar_ts: datetime
    # Structured explainability for API/UI (kind/summary chip + full detail)
    reason_kind: str = "hybrid"
    reason_summary: str = ""
    reason_detail: str = ""


@dataclass(frozen=True)
class OptionLeg:
    tradingsymbol: str
    option_type: Literal["CE", "PE"]
    strike: float
    expiry: date
    qty_lots: int
    limit_price: float | None
    instrument_key: str


@dataclass(frozen=True)
class OptionsRecommendation:
    symbol: str
    legs: list[OptionLeg]
    expiry: date
    spot_ref: float
    rationale: str
    chain_meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OrderResult:
    client_order_id: str
    status: str
    mode: TradingMode
    broker_order_id: str | None
    message: str | None
