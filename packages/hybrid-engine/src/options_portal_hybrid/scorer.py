"""Weighted hybrid scorer with news veto rules."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from options_portal_domain.types import (
    HybridDecision,
    NewsItem,
    SentimentResult,
    SignalAction,
    TechnicalSignal,
)
from options_portal_hybrid.explain import build_reason_fields


class WeightedHybridScorer:
    def __init__(
        self,
        *,
        weight_tech: float = 0.65,
        weight_news: float = 0.35,
        enter_long: float = 0.45,
        enter_short: float = -0.45,
        news_veto_abs: float = 0.70,
    ) -> None:
        self.weight_tech = weight_tech
        self.weight_news = weight_news
        self.enter_long = enter_long
        self.enter_short = enter_short
        self.news_veto_abs = news_veto_abs

    def combine(
        self,
        tech: TechnicalSignal,
        sentiments: Sequence[SentimentResult],
        news: Sequence[NewsItem],
        context: dict[str, Any],
    ) -> HybridDecision:
        _ = context
        news_score, news_conf = _aggregate_news(sentiments)
        hybrid = (
            self.weight_tech * tech.score * tech.confidence
            + self.weight_news * news_score * news_conf
        )
        hybrid = max(-1.0, min(1.0, hybrid))
        reasons = list(tech.reasons)
        news_ids = [s.news_id for s in sentiments]

        # News veto: strong opposing news blocks TA-driven entries
        veto = False
        if abs(news_score) >= self.news_veto_abs and _sign(news_score) != _sign(tech.score):
            if tech.action in {SignalAction.BUY_CE_BIAS, SignalAction.BUY_PE_BIAS}:
                veto = True
                reasons.append(
                    f"News veto: |news|={news_score:.2f} conflicts with TA → HOLD"
                )

        if veto:
            action = SignalAction.HOLD
        elif hybrid >= self.enter_long:
            action = SignalAction.BUY_CE_BIAS
            reasons.append(f"Hybrid {hybrid:.2f} ≥ enter_long {self.enter_long}")
        elif hybrid <= self.enter_short:
            action = SignalAction.BUY_PE_BIAS
            reasons.append(f"Hybrid {hybrid:.2f} ≤ enter_short {self.enter_short}")
        else:
            action = SignalAction.HOLD
            reasons.append(f"Hybrid {hybrid:.2f} in neutral band")

        if news:
            reasons.append(f"News context: {len(news)} item(s), score {news_score:.2f}")
            for item in list(news)[:2]:
                title = (item.title or "").strip()
                if title:
                    reasons.append(f"Headline: {title[:120]}")

        confidence = min(1.0, (tech.confidence + news_conf) / 2 + abs(hybrid) * 0.2)
        tech_score = tech.score
        news_rounded = round(news_score, 6)
        hybrid_rounded = round(hybrid, 6)
        explain = build_reason_fields(
            action=action,
            tech_score=tech_score,
            news_score=news_rounded,
            hybrid_score=hybrid_rounded,
            reasons=reasons,
            tech_reasons=tech.reasons,
        )
        return HybridDecision(
            symbol=tech.symbol,
            action=action,
            tech_score=tech_score,
            news_score=news_rounded,
            hybrid_score=hybrid_rounded,
            confidence=round(confidence, 4),
            reasons=reasons,
            news_ids=news_ids,
            bar_ts=tech.bar_ts,
            reason_kind=explain["reason_kind"],
            reason_summary=explain["reason_summary"],
            reason_detail=explain["reason_detail"],
        )


def _aggregate_news(sentiments: Sequence[SentimentResult]) -> tuple[float, float]:
    if not sentiments:
        return 0.0, 0.0
    weighted = sum(s.score * s.confidence for s in sentiments)
    conf = sum(s.confidence for s in sentiments) / len(sentiments)
    score = weighted / max(sum(s.confidence for s in sentiments), 1e-9)
    return max(-1.0, min(1.0, score)), conf


def _sign(x: float) -> int:
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0
