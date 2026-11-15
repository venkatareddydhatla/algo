"""Hybrid decision engine + single-leg options recommender."""

from options_portal_hybrid.explain import (
    action_from_score,
    attach_reason_fields,
    build_reason_fields,
    classify_reason_kind,
    is_actionable,
    pick_top_hybrid_sides,
    sort_signal_rows,
)
from options_portal_hybrid.options_recommender import SingleLegATMRecommender
from options_portal_hybrid.scorer import WeightedHybridScorer

__all__ = [
    "SingleLegATMRecommender",
    "WeightedHybridScorer",
    "action_from_score",
    "attach_reason_fields",
    "build_reason_fields",
    "classify_reason_kind",
    "is_actionable",
    "pick_top_hybrid_sides",
    "sort_signal_rows",
]
