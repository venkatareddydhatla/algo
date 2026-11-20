"""Build structured reason fields for hybrid / mock signal rows.

Classification follows what actually drove the action:
- technical: TA alone yields the same actionable decision (news filler omitted)
- news: news was required / dominant for the decision
- hybrid: both tech and news meaningfully contribute (or both alone clear)
- veto: news veto blocked a TA entry
- neutral: HOLD in the neutral band
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from options_portal_domain.types import SignalAction

_HOLD_ACTIONS = frozenset({SignalAction.HOLD.value, "HOLD", ""})

_TECH_LABELS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"EMA9|EMA\s*cross|ema_cross", re.I), "EMA"),
    (re.compile(r"RSI", re.I), "RSI"),
    (re.compile(r"Supertrend", re.I), "ST"),
    (re.compile(r"VWAP", re.I), "VWAP"),
)
_NEWS_LABELS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"earning", re.I), "earnings"),
    (re.compile(r"news\s*veto|conflict", re.I), "veto"),
    (re.compile(r"\bnews\b|sentiment|headline|inflow|broker", re.I), "news"),
)

_TECH_LINE = re.compile(
    r"EMA|RSI|Supertrend|VWAP|confluence|technical",
    re.I,
)
_NEWS_LINE = re.compile(
    r"\bnews\b|headline|sentiment|veto|earnings|inflow|broker",
    re.I,
)
_HYBRID_LINE = re.compile(r"\bhybrid\b|enter_long|enter_short|neutral band", re.I)


def is_actionable(action: str | SignalAction | None) -> bool:
    if action is None:
        return False
    value = action.value if isinstance(action, SignalAction) else str(action)
    return value.upper() not in _HOLD_ACTIONS


def action_from_score(
    score: float,
    *,
    enter_long: float = 0.45,
    enter_short: float = -0.45,
) -> str:
    if score >= enter_long:
        return SignalAction.BUY_CE_BIAS.value
    if score <= enter_short:
        return SignalAction.BUY_PE_BIAS.value
    return SignalAction.HOLD.value


def classify_reason_kind(
    *,
    action: str | SignalAction,
    tech_score: float,
    news_score: float,
    hybrid_score: float,
    weight_tech: float = 0.65,
    weight_news: float = 0.35,
    enter_long: float = 0.45,
    enter_short: float = -0.45,
    tech_confidence: float = 1.0,
    news_confidence: float = 1.0,
    vetoed: bool = False,
) -> str:
    """Decide reason_kind from whether tech/news actually drove the action."""
    action_s = action.value if isinstance(action, SignalAction) else str(action)
    if vetoed:
        return "veto"
    if not is_actionable(action_s):
        blob_hold = abs(news_score) >= 0.15 and _sign(news_score) != _sign(tech_score)
        if blob_hold and abs(news_score) >= 0.70:
            return "veto"
        return "neutral"

    tech_conf = tech_confidence if tech_confidence > 0 else 1.0
    news_conf = news_confidence if news_confidence > 0 else 0.0
    tech_alone = weight_tech * tech_score * tech_conf
    news_alone = weight_news * news_score * news_conf

    tech_alone_action = action_from_score(
        tech_alone, enter_long=enter_long, enter_short=enter_short
    )
    news_alone_action = action_from_score(
        news_alone, enter_long=enter_long, enter_short=enter_short
    )
    final = action_s.upper()

    tech_drives = tech_alone_action == final
    # News tips the decision when TA alone would not produce the same action,
    # or when news alone would clear the same entry band.
    news_tipped = tech_alone_action != final
    news_alone_clears = news_alone_action == final
    news_drives = news_alone_clears or news_tipped

    if tech_drives and news_alone_clears:
        # Both sides independently clear the same actionable decision.
        return "hybrid"
    if tech_drives and not news_tipped:
        # Same action from TA alone; dry-run news filler must not force HYBRID.
        return "technical"
    if news_drives and not tech_drives:
        # News was required to reach this action.
        if _same_direction(tech_score, hybrid_score) and abs(tech_score) >= 0.15:
            return "hybrid"
        return "news"
    if tech_drives and news_tipped:
        # Uncommon path; treat as hybrid if both non-trivial.
        return "hybrid"
    return "hybrid"


def build_reason_fields(
    *,
    action: str | SignalAction,
    tech_score: float,
    news_score: float,
    hybrid_score: float,
    reasons: Sequence[str] | None = None,
    tech_reasons: Sequence[str] | None = None,
    weight_tech: float = 0.65,
    weight_news: float = 0.35,
    enter_long: float = 0.45,
    enter_short: float = -0.45,
    tech_confidence: float = 1.0,
    news_confidence: float = 1.0,
    vetoed: bool = False,
) -> dict[str, str]:
    """Return reason_kind / reason_summary / reason_detail for API + UI.

    reason_detail only includes bullets that match the classified kind
    (technical -> tech only; news -> news only; hybrid -> both sections).
    """
    action_s = action.value if isinstance(action, SignalAction) else str(action)
    all_reasons = [str(r) for r in (reasons or []) if r]
    tech_only = [str(r) for r in (tech_reasons or []) if r]
    if not all_reasons and tech_only:
        all_reasons = list(tech_only)

    kind = classify_reason_kind(
        action=action_s,
        tech_score=tech_score,
        news_score=news_score,
        hybrid_score=hybrid_score,
        weight_tech=weight_tech,
        weight_news=weight_news,
        enter_long=enter_long,
        enter_short=enter_short,
        tech_confidence=tech_confidence,
        news_confidence=news_confidence,
        vetoed=vetoed
        or _looks_like_veto(all_reasons, action_s),
    )

    tech_bullets, news_bullets, other = _partition_reasons(all_reasons)
    if not tech_bullets and tech_only:
        tech_bullets = list(tech_only)

    detail = _format_detail(
        kind=kind,
        tech_bullets=tech_bullets,
        news_bullets=news_bullets,
        other=other,
        action=action_s,
        tech_score=tech_score,
        news_score=news_score,
        hybrid_score=hybrid_score,
    )
    summary = _summary_label(kind, tech_bullets, news_bullets)
    return {
        "reason_kind": kind,
        "reason_summary": summary,
        "reason_detail": detail,
    }


def _looks_like_veto(reasons: Sequence[str], action: str) -> bool:
    if is_actionable(action):
        return False
    blob = " ".join(reasons).lower()
    return "veto" in blob or "conflict" in blob


def _partition_reasons(
    reasons: Sequence[str],
) -> tuple[list[str], list[str], list[str]]:
    tech: list[str] = []
    news: list[str] = []
    other: list[str] = []
    for text in reasons:
        explicit_news = bool(re.search(r"\bnews\b|headline|veto|earnings", text, re.I))
        is_tech = bool(_TECH_LINE.search(text))
        is_news = bool(_NEWS_LINE.search(text))
        if explicit_news:
            news.append(text)
        elif is_tech:
            tech.append(text)
        elif is_news:
            news.append(text)
        elif _HYBRID_LINE.search(text):
            other.append(text)
        else:
            other.append(text)
    return tech, news, other


def _format_detail(
    *,
    kind: str,
    tech_bullets: list[str],
    news_bullets: list[str],
    other: list[str],
    action: str,
    tech_score: float,
    news_score: float,
    hybrid_score: float,
) -> str:
    threshold = [o for o in other if _HYBRID_LINE.search(o)]
    misc = [o for o in other if not _HYBRID_LINE.search(o)]

    if kind == "technical":
        lines = tech_bullets + threshold
        return _section("Technical", lines) or _fallback_detail(
            action, tech_score, news_score, hybrid_score
        )
    if kind == "news":
        lines = news_bullets + threshold
        return _section("News", lines) or _fallback_detail(
            action, tech_score, news_score, hybrid_score
        )
    if kind == "veto":
        lines = news_bullets + tech_bullets + threshold + misc
        return _section("Veto", lines) or _fallback_detail(
            action, tech_score, news_score, hybrid_score
        )
    if kind == "neutral":
        lines = tech_bullets + threshold + misc
        # Neutral: omit mild news filler unless it is the conflict story.
        if any("conflict" in n.lower() or "veto" in n.lower() for n in news_bullets):
            lines = news_bullets + lines
        return _section("Neutral", lines) or _fallback_detail(
            action, tech_score, news_score, hybrid_score
        )
    # hybrid: both sections clearly
    parts: list[str] = []
    tech_sec = _section("Technical", tech_bullets)
    news_sec = _section("News", news_bullets)
    if tech_sec:
        parts.append(tech_sec)
    if news_sec:
        parts.append(news_sec)
    if threshold:
        parts.append(_section("Decision", threshold))
    if not parts:
        return _fallback_detail(action, tech_score, news_score, hybrid_score)
    return "\n\n".join(parts)


def _section(title: str, bullets: Sequence[str]) -> str:
    clean = [b.strip() for b in bullets if b and b.strip()]
    if not clean:
        return ""
    body = "\n".join(f"- {b}" for b in clean)
    return f"{title}:\n{body}"


def _summary_label(
    kind: str,
    tech_bullets: Sequence[str],
    news_bullets: Sequence[str],
) -> str:
    tech_bits: list[str] = []
    news_bits: list[str] = []
    for text in tech_bullets:
        for pattern, label in _TECH_LABELS:
            if pattern.search(text) and label not in tech_bits:
                tech_bits.append(label)
    for text in news_bullets:
        for pattern, label in _NEWS_LABELS:
            if pattern.search(text) and label not in news_bits:
                news_bits.append(label)

    if kind == "veto":
        return "news veto"
    if kind == "neutral":
        return "neutral"
    if kind == "technical":
        return "+".join(tech_bits[:3]) if tech_bits else "technical"
    if kind == "news":
        return "+".join(news_bits[:2]) if news_bits else "news"
    # hybrid
    if tech_bits and news_bits:
        left = "+".join(tech_bits[:2])
        right = news_bits[0]
        if right == "news":
            return f"{left}+news"
        return f"{right}+tech"
    if tech_bits:
        return "+".join(tech_bits[:2]) + "+news"
    if news_bits:
        return "+".join(news_bits[:2]) + "+tech"
    return "hybrid"


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


def _sign(x: float) -> int:
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0


def _same_direction(a: float, b: float) -> bool:
    return _sign(a) != 0 and _sign(a) == _sign(b)


def attach_reason_fields(
    row: dict[str, Any],
    *,
    force: bool = True,
) -> dict[str, Any]:
    """Ensure a signal dict has reason_* keys.

    Default force=True so classification stays consistent with scores even when
    older reason_* stubs are present on seed rows.
    """
    if (
        not force
        and row.get("reason_kind")
        and row.get("reason_summary")
        and row.get("reason_detail")
    ):
        return row
    fields = build_reason_fields(
        action=str(row.get("action") or "HOLD"),
        tech_score=float(row.get("tech_score") or 0.0),
        news_score=float(row.get("news_score") or 0.0),
        hybrid_score=float(row.get("hybrid_score") or 0.0),
        reasons=row.get("reasons") or [],
        weight_tech=float(row.get("weight_tech") or 0.65),
        weight_news=float(row.get("weight_news") or 0.35),
        enter_long=float(row.get("enter_long") or 0.45),
        enter_short=float(row.get("enter_short") or -0.45),
        tech_confidence=float(row.get("tech_confidence") or 1.0),
        news_confidence=float(
            row.get("news_confidence")
            if row.get("news_confidence") is not None
            else (1.0 if abs(float(row.get("news_score") or 0.0)) > 0 else 0.0)
        ),
        vetoed=bool(row.get("vetoed") or False),
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


def _hybrid_strip_rank(row: dict[str, Any], *, positive: bool) -> tuple:
    """Prefer Pine BUY/SELL (or legacy hybrid), then strongest |score|."""
    hybrid = float(row.get("hybrid_score") or 0.0)
    action = str(row.get("action") or "HOLD").upper()
    if positive and action not in {"BUY", "BUY_CE_BIAS"} and hybrid <= 0:
        return (9, 0.0, 0.0, "")
    if not positive and action not in {"SELL", "BUY_PE_BIAS"} and hybrid >= 0:
        return (9, 0.0, 0.0, "")
    kind = str(row.get("reason_kind") or "").lower()
    kind_rank = 0 if kind in {"pine", "hybrid"} else 1
    actionable_rank = 0 if is_actionable(action) else 1
    confidence = float(row.get("confidence") or 0.0)
    symbol = str(row.get("symbol") or "")
    signed = hybrid if positive else -hybrid
    return (kind_rank, actionable_rank, -signed, -confidence, symbol)


def pick_top_hybrid_sides(
    rows: Sequence[dict[str, Any]],
    *,
    limit: int = 5,
) -> dict[str, list[dict[str, Any]]]:
    """Top BUY (ce) and SELL (pe) strip candidates for the watchlist header.

    Prefers Pine ``BUY`` / ``SELL`` actions; falls back to score sign.
    """
    limit = max(1, min(int(limit), 20))
    buys = [
        r for r in rows
        if str(r.get("action") or "").upper() in {"BUY", "BUY_CE_BIAS"}
        or float(r.get("hybrid_score") or 0.0) > 0
    ]
    sells = [
        r for r in rows
        if str(r.get("action") or "").upper() in {"SELL", "BUY_PE_BIAS"}
        or float(r.get("hybrid_score") or 0.0) < 0
    ]
    ce = sorted(buys, key=lambda r: _hybrid_strip_rank(r, positive=True))[:limit]
    pe = sorted(sells, key=lambda r: _hybrid_strip_rank(r, positive=False))[:limit]
    return {"ce": ce, "pe": pe}
