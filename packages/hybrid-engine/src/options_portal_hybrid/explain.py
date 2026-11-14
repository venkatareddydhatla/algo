"""Build structured reason fields for hybrid / mock signal rows."""

from __future__ import annotations

import re
from typing import Any, Sequence

from options_portal_domain.types import SignalAction

_HOLD_ACTIONS = frozenset({SignalAction.HOLD.value, "HOLD", ""})

# Short labels mapped from common reason snippets
_TECH_LABELS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"EMA9|EMA\s*cross|ema_cross", re.I), "EMA"),
    (re.compile(r"RSI", re.I), "RSI"),
    (re.compile(r"Supertrend", re.I), "ST"),
    (re.compile(r"VWAP", re.I), "VWAP"),
)
_NEWS_LABELS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"earning", re.I), "earnings"),
    (re.compile(r"news\s*veto|conflict", re.I), "veto"),
    (re.compile(r"news|sentiment|headline|inflow|broker", re.I), "news"),
)


def is_actionable(action: str | SignalAction | None) -> bool:
    if action is None:
        return False
    value = action.value if isinstance(action, SignalAction) else str(action)
    return value.upper() not in _HOLD_ACTIONS


def build_reason_fields(
    *,
    action: str | SignalAction,
    tech_score: float,
    news_score: float,
    hybrid_score: float,
    reasons: Sequence[str] | None = None,
    tech_reasons: Sequence[str] | None = None,
) -> dict[str, str]:
    """Return reason_kind / reason_summary / reason_detail for API + UI.

    kind is one of: technical, news, hybrid, veto, neutral.
    summary is a short chip label (e.g. EMA+RSI, news+tech, hybrid).
    detail is the full explainability text.
    """
    action_s = action.value if isinstance(action, SignalAction) else str(action)
    all_reasons = [str(r) for r in (reasons or []) if r]
    tech_only = [str(r) for r in (tech_reasons or []) if r]
    joined = all_reasons or tech_only
    detail = "; ".join(joined) if joined else _fallback_detail(
        action_s, tech_score, news_score, hybrid_score
    )

    blob = " ".join(joined).lower()
    has_veto = "veto" in blob or "conflict" in blob
    has_news = abs(news_score) >= 0.15 or any(
        p.search(r) for r in joined for p, _ in _NEWS_LABELS
    )
    has_tech = abs(tech_score) >= 0.15 or any(
        p.search(r) for r in joined for p, _ in _TECH_LABELS
    )

    if has_veto and not is_actionable(action_s):
        kind = "veto"
    elif has_tech and has_news:
        kind = "hybrid"
    elif has_news and not has_tech:
        kind = "news"
    elif has_tech and not has_news:
        kind = "technical"
    elif is_actionable(action_s):
        kind = "hybrid"
    else:
        kind = "neutral"

    summary = _summary_label(kind, joined, tech_score, news_score)
    return {
        "reason_kind": kind,
        "reason_summary": summary,
        "reason_detail": detail,
    }


def _summary_label(
    kind: str,
    reasons: Sequence[str],
    tech_score: float,
    news_score: float,
) -> str:
    tech_bits: list[str] = []
    news_bits: list[str] = []
    for text in reasons:
        for pattern, label in _TECH_LABELS:
            if pattern.search(text) and label not in tech_bits:
                tech_bits.append(label)
        for pattern, label in _NEWS_LABELS:
            if pattern.search(text) and label not in news_bits:
                news_bits.append(label)

    if kind == "veto":
        return "news veto"
    if kind == "neutral":
        return "neutral"
    if tech_bits and news_bits:
        # Prefer short compound like earnings+tech or EMA+RSI+news
        left = "+".join(tech_bits[:2])
        right = news_bits[0]
        if right == "news" and left:
            return f"{left}+news"
        return f"{right}+tech" if right != "news" else f"{left}+news"
    if tech_bits:
        return "+".join(tech_bits[:3])
    if news_bits:
        return "+".join(news_bits[:2])
    if kind == "technical":
        return "technical"
    if kind == "news":
        return "news"
    if abs(tech_score) >= abs(news_score):
        return "hybrid"
    return "news+tech" if abs(news_score) > 0.01 else "hybrid"


def _fallback_detail(
    action: str,
    tech_score: float,
    news_score: float,
    hybrid_score: float,
) -> str:
    return (
        f"action={action}; tech={tech_score:.2f}; news={news_score:.2f}; "
        f"hybrid={hybrid_score:.2f}"
    )


def attach_reason_fields(row: dict[str, Any]) -> dict[str, Any]:
    """Ensure a signal dict has reason_* keys (idempotent)."""
    if row.get("reason_kind") and row.get("reason_summary") and row.get("reason_detail"):
        return row
    fields = build_reason_fields(
        action=str(row.get("action") or "HOLD"),
        tech_score=float(row.get("tech_score") or 0.0),
        news_score=float(row.get("news_score") or 0.0),
        hybrid_score=float(row.get("hybrid_score") or 0.0),
        reasons=row.get("reasons") or [],
    )
    out = dict(row)
    out.update(fields)
    return out


def sort_signal_rows(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Actionable first, then |hybrid| desc, confidence desc, symbol asc."""

    def key(row: dict[str, Any]) -> tuple:
        action = str(row.get("action") or "HOLD")
        actionable_rank = 0 if is_actionable(action) else 1
        hybrid_abs = abs(float(row.get("hybrid_score") or 0.0))
        confidence = float(row.get("confidence") or 0.0)
        symbol = str(row.get("symbol") or "")
        return (actionable_rank, -hybrid_abs, -confidence, symbol)

    return sorted(rows, key=key)
