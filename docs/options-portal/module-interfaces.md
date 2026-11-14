# Module interfaces (replaceable plugins)

Language-agnostic contracts. Python-oriented sketches below; a TypeScript API can mirror the same shapes via OpenAPI shared types.

---

## Core domain types

```python
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Literal, Protocol, Sequence

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
    ts: datetime          # bar open, aware UTC
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
    score: float          # [-1, 1]
    confidence: float     # [0, 1]
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
    chain_meta: dict[str, Any]

@dataclass(frozen=True)
class OrderResult:
    client_order_id: str
    status: str
    mode: TradingMode
    broker_order_id: str | None
    message: str | None
```

---

## BrokerAdapter

```python
class BrokerAdapter(Protocol):
    name: str  # "upstox"

    async def ensure_auth(self) -> None: ...
    async def get_candles(
        self, symbol: str, timeframe: Timeframe,
        start: datetime, end: datetime,
    ) -> Sequence[Candle]: ...
    async def get_option_chain(
        self, symbol: str, expiry: date,
    ) -> dict[str, Any]: ...
    async def place_order(self, order: dict[str, Any]) -> OrderResult: ...
    async def cancel_order(self, broker_order_id: str) -> OrderResult: ...
    async def health(self) -> dict[str, Any]: ...
```

Upstox-specific auth (OAuth code → access/refresh) lives **inside** `broker-upstox`, not in the orchestrator.

---

## TA & signals

```python
class IndicatorPlugin(Protocol):
    name: str
    version: str

    def compute(self, candles: Sequence[Candle]) -> dict[str, float | None]: ...

class SignalGenerator(Protocol):
    name: str

    def evaluate(
        self, symbol: str, candles: Sequence[Candle],
        features: dict[str, Any],
    ) -> TechnicalSignal: ...
```

Plugins register via entry points / a simple registry:

```python
ta_registry.register(RSIPlugin(period=14))
ta_registry.register(EMAPlugin(fast=9, slow=21))
ta_registry.register(SupertrendPlugin())
```

---

## News & sentiment

```python
class NewsIngestor(Protocol):
    source: str

    async def fetch_since(self, since: datetime) -> Sequence[NewsItem]: ...

class SentimentScorer(Protocol):
    model: str

    async def score_batch(
        self, items: Sequence[NewsItem],
    ) -> Sequence[SentimentResult]: ...
```

Default composition: `VaderScorer` always on; `FinBertScorer` optional behind config `sentiment.finbert.enabled`.

---

## Hybrid + options + risk + orders

```python
class HybridScorer(Protocol):
    def combine(
        self,
        tech: TechnicalSignal,
        sentiments: Sequence[SentimentResult],
        news: Sequence[NewsItem],
        context: dict[str, Any],  # session, calendar, volatility regime
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
```

---

## Notifier & clock

```python
class Notifier(Protocol):
    async def send(self, title: str, body: str, meta: dict[str, Any]) -> None: ...

class MarketCalendar(Protocol):
    def is_trading_day(self, d: date) -> bool: ...
    def session_open_close_ist(self, d: date) -> tuple[datetime, datetime]: ...
    def next_weekly_expiry(self, symbol: str, as_of: date) -> date: ...
    def next_monthly_expiry(self, symbol: str, as_of: date) -> date: ...
```

`IndiaMarketCalendar` encodes NSE holidays and F&O expiry conventions (config-overridable for exchange circular changes).

---

## Extension rules

1. New broker → implement `BrokerAdapter`; no changes to hybrid engine.
2. New indicator → `IndicatorPlugin` + register; optional SignalGenerator tweak.
3. New news source → `NewsIngestor` + source allowlist in config.
4. Breaking type changes → bump `model_versions` in `signals` rows and document in CHANGELOG.
