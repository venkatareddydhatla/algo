"""Unit tests for hybrid scorer veto rules and order-router live block."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from options_portal_domain.types import (
    HybridDecision,
    NewsItem,
    OptionLeg,
    OptionsRecommendation,
    SentimentResult,
    SignalAction,
    TechnicalSignal,
)
from options_portal_hybrid.scorer import WeightedHybridScorer
from options_portal_risk.order_router import SafeOrderRouter


def _tech(score: float, action: SignalAction) -> TechnicalSignal:
    return TechnicalSignal(
        symbol="NIFTY",
        timeframe="15m",
        bar_ts=datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc),
        action=action,
        score=score,
        confidence=0.8,
        reasons=["unit"],
        features={},
    )


def test_hybrid_news_veto_blocks_conflicting_ta():
    tech = _tech(0.7, SignalAction.BUY_CE_BIAS)
    sentiments = [
        SentimentResult(
            news_id="n1",
            model="vader",
            score=-0.85,
            label="bearish",
            confidence=0.9,
        )
    ]
    news = [
        NewsItem(
            id="n1",
            source="et",
            title="Sharp selloff",
            body="Markets tumble",
            published_at=datetime.now(tz=timezone.utc),
            symbols=["NIFTY"],
        )
    ]
    scorer = WeightedHybridScorer(news_veto_abs=0.70)
    decision = scorer.combine(tech, sentiments, news, {})
    assert decision.action == SignalAction.HOLD
    assert any("veto" in r.lower() for r in decision.reasons)


def test_hybrid_aligned_scores_enter_long():
    tech = _tech(0.8, SignalAction.BUY_CE_BIAS)
    sentiments = [
        SentimentResult(
            news_id="n2",
            model="vader",
            score=0.4,
            label="bullish",
            confidence=0.7,
        )
    ]
    decision = WeightedHybridScorer(enter_long=0.45).combine(tech, sentiments, [], {})
    assert decision.action == SignalAction.BUY_CE_BIAS
    assert decision.hybrid_score > 0


def test_hybrid_neutral_band_holds():
    tech = _tech(0.1, SignalAction.HOLD)
    decision = WeightedHybridScorer().combine(tech, [], [], {})
    assert decision.action == SignalAction.HOLD


@pytest.mark.asyncio
async def test_order_router_blocks_live_in_v1():
    reco = OptionsRecommendation(
        symbol="NIFTY",
        legs=[
            OptionLeg(
                tradingsymbol="NIFTY24500CE",
                option_type="CE",
                strike=24500,
                expiry=date(2026, 11, 20),
                qty_lots=1,
                limit_price=100.0,
                instrument_key="NSE_FO|NIFTY|CE|24500",
            )
        ],
        expiry=date(2026, 11, 20),
        spot_ref=24500,
        rationale="test",
        chain_meta={"oi": 90000},
    )
    router = SafeOrderRouter(v1_block_live=True)
    result = await router.execute(reco, "live", actor_user_id=None)
    assert result.status == "rejected"
    assert "live" in (result.message or "").lower()


@pytest.mark.asyncio
async def test_order_router_dry_run_simulates():
    reco = OptionsRecommendation(
        symbol="TCS",
        legs=[
            OptionLeg(
                tradingsymbol="TCS3900CE",
                option_type="CE",
                strike=3900,
                expiry=date(2026, 11, 27),
                qty_lots=1,
                limit_price=50.0,
                instrument_key="NSE_FO|TCS|CE|3900",
            )
        ],
        expiry=date(2026, 11, 27),
        spot_ref=3900,
        rationale="test",
        chain_meta={"oi": 90000},
    )
    result = await SafeOrderRouter().execute(
        reco,
        "dry_run",
        actor_user_id=None,
        limits={"max_lots_per_symbol": 2, "min_option_oi": 50000},
    )
    assert result.status == "simulated"
    assert result.mode == "dry_run"
