"""VADER-based sentiment scorer (default for v1)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from options_portal_domain.types import NewsItem, SentimentResult

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
except ImportError:  # pragma: no cover - fallback for bare envs
    SentimentIntensityAnalyzer = None  # type: ignore[misc, assignment]


def _label(score: float) -> Literal["bullish", "bearish", "neutral"]:
    if score >= 0.05:
        return "bullish"
    if score <= -0.05:
        return "bearish"
    return "neutral"


class VaderScorer:
    model = "vader"

    def __init__(self) -> None:
        if SentimentIntensityAnalyzer is None:
            self._analyzer = None
        else:
            self._analyzer = SentimentIntensityAnalyzer()

    async def score_batch(self, items: Sequence[NewsItem]) -> Sequence[SentimentResult]:
        results: list[SentimentResult] = []
        for item in items:
            text = f"{item.title}. {item.body}".strip()
            if self._analyzer is None:
                # Lightweight lexical fallback if vaderSentiment not installed
                score = _lex_fallback(text)
            else:
                compound = float(self._analyzer.polarity_scores(text)["compound"])
                score = compound
            results.append(
                SentimentResult(
                    news_id=item.id,
                    model=self.model,
                    score=round(score, 6),
                    label=_label(score),
                    confidence=min(1.0, abs(score) + 0.3),
                )
            )
        return results


def _lex_fallback(text: str) -> float:
    pos = ("rally", "surge", "gain", "bull", "inflow", "upgrade", "strong")
    neg = ("fall", "drop", "cut", "bear", "outflow", "downgrade", "weak", "fraud")
    lower = text.lower()
    p = sum(1 for w in pos if w in lower)
    n = sum(1 for w in neg if w in lower)
    if p == n == 0:
        return 0.0
    return max(-1.0, min(1.0, (p - n) / max(p + n, 1)))
