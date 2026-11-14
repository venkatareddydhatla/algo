"""Hybrid decision engine + single-leg options recommender."""

from options_portal_hybrid.explain import (
    attach_reason_fields,
    build_reason_fields,
    is_actionable,
    sort_signal_rows,
)
from options_portal_hybrid.options_recommender import SingleLegATMRecommender
from options_portal_hybrid.scorer import WeightedHybridScorer

__all__ = [
    "SingleLegATMRecommender",
    "WeightedHybridScorer",
    "attach_reason_fields",
    "build_reason_fields",
    "is_actionable",
    "sort_signal_rows",
]

__all__ = ["SingleLegATMRecommender", "WeightedHybridScorer"]
