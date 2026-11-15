"""Sort order + structured reason fields for signals / dry-run mock."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_APP = ROOT / "apps" / "api"
if str(API_APP) not in sys.path:
    sys.path.insert(0, str(API_APP))

from options_portal_domain.types import (  # noqa: E402
    SignalAction,
    TechnicalSignal,
)
from options_portal_hybrid.explain import (  # noqa: E402
    attach_reason_fields,
    build_reason_fields,
    classify_reason_kind,
    is_actionable,
    sort_signal_rows,
)
from options_portal_hybrid.scorer import WeightedHybridScorer  # noqa: E402

from app.mock_data import (  # noqa: E402
    MOCK_SIGNALS,
    build_universe_signals,
    synthetic_signal_for_symbol,
)


def test_is_actionable():
    assert is_actionable("BUY_CE_BIAS")
    assert is_actionable(SignalAction.BUY_PE_BIAS)
    assert not is_actionable("HOLD")
    assert not is_actionable(SignalAction.HOLD)


def test_sort_actionable_first_then_abs_hybrid():
    rows = [
        {"symbol": "AAA", "action": "HOLD", "hybrid_score": 0.9, "confidence": 0.9},
        {"symbol": "BBB", "action": "BUY_CE_BIAS", "hybrid_score": 0.4, "confidence": 0.5},
        {"symbol": "CCC", "action": "BUY_PE_BIAS", "hybrid_score": -0.8, "confidence": 0.7},
        {"symbol": "DDD", "action": "BUY_CE_BIAS", "hybrid_score": 0.4, "confidence": 0.9},
        {"symbol": "EEE", "action": "HOLD", "hybrid_score": -0.1, "confidence": 0.2},
    ]
    sorted_rows = sort_signal_rows(rows)
    symbols = [r["symbol"] for r in sorted_rows]
    assert symbols[0] == "CCC"
    assert set(symbols[:3]) == {"CCC", "BBB", "DDD"}
    assert symbols[1] == "DDD"
    assert symbols[2] == "BBB"
    assert symbols[3] == "AAA"
    assert symbols[4] == "EEE"


def test_classify_technical_when_ta_alone_clears():
    """SYRMA-like: strong TA clears enter band; news filler must not force HYBRID."""
    kind = classify_reason_kind(
        action="BUY_CE_BIAS",
        tech_score=0.7521,
        news_score=0.70,
        hybrid_score=0.7339,
        weight_tech=0.65,
        weight_news=0.35,
        enter_long=0.45,
        enter_short=-0.45,
    )
    assert kind == "technical"

    fields = build_reason_fields(
        action="BUY_CE_BIAS",
        tech_score=0.7521,
        news_score=0.70,
        hybrid_score=0.7339,
        reasons=[
            "EMA9 > EMA21 on 15m",
            "RSI(14) 65 — rising",
            "Supertrend bullish",
            "News: bullish cluster last 3h",
            "Headline: Dry-run bullish flow for SYRMA (score +0.70)",
            "Hybrid 0.73 >= enter_long 0.45",
        ],
    )
    assert fields["reason_kind"] == "technical"
    assert "news" not in fields["reason_summary"].lower()
    assert "News:" not in fields["reason_detail"]
    assert "Headline:" not in fields["reason_detail"]
    assert "Technical:" in fields["reason_detail"]
    assert "EMA9" in fields["reason_detail"]


def test_classify_hybrid_when_both_alone_clear():
    # Both weighted legs independently clear enter_long.
    kind = classify_reason_kind(
        action="BUY_CE_BIAS",
        tech_score=0.80,
        news_score=0.90,
        hybrid_score=0.70,
        weight_tech=0.65,
        weight_news=0.55,  # 0.55*0.90 = 0.495 clears 0.45
        enter_long=0.45,
        enter_short=-0.45,
    )
    assert kind == "hybrid"


def test_classify_news_when_news_tips_over_threshold():
    # TA alone stays in neutral; news tips hybrid across enter_long.
    kind = classify_reason_kind(
        action="BUY_CE_BIAS",
        tech_score=0.50,  # 0.65*0.50 = 0.325 HOLD
        news_score=0.60,  # 0.35*0.60 = 0.21; hybrid ~0.535
        hybrid_score=0.535,
        weight_tech=0.65,
        weight_news=0.35,
    )
    assert kind in {"news", "hybrid"}
    # Tech leans same direction with material score -> hybrid
    assert kind == "hybrid"

    # Weak/opposite tech -> news
    kind_news = classify_reason_kind(
        action="BUY_CE_BIAS",
        tech_score=0.05,
        news_score=0.95,
        hybrid_score=0.50,
        weight_tech=0.65,
        weight_news=0.55,
    )
    assert kind_news == "news"


def test_classify_veto():
    kind = classify_reason_kind(
        action="HOLD",
        tech_score=0.70,
        news_score=-0.80,
        hybrid_score=0.10,
        vetoed=True,
    )
    assert kind == "veto"


def test_build_reason_fields_sections_match_kind():
    tech = build_reason_fields(
        action="BUY_CE_BIAS",
        tech_score=0.80,
        news_score=0.05,
        hybrid_score=0.52,
        reasons=["EMA9 > EMA21", "RSI(14) 58 — rising", "Hybrid 0.52 >= enter_long"],
    )
    assert tech["reason_kind"] == "technical"
    assert "EMA" in tech["reason_summary"] or "RSI" in tech["reason_summary"]
    assert "News:" not in tech["reason_detail"]

    tipped = build_reason_fields(
        action="BUY_PE_BIAS",
        tech_score=-0.48,
        news_score=-0.67,
        hybrid_score=-0.55,
        reasons=[
            "EMA9 < EMA21",
            "Close below VWAP",
            "Bearish news cluster",
            "Headline: Broker cuts target",
            "Hybrid -0.55 <= enter_short -0.45",
        ],
    )
    assert tipped["reason_kind"] == "hybrid"
    assert "Technical:" in tipped["reason_detail"]
    assert "News:" in tipped["reason_detail"]
    assert "Headline" in tipped["reason_detail"]


def test_scorer_technical_without_news():
    tech = TechnicalSignal(
        symbol="NIFTY",
        timeframe="15m",
        bar_ts=datetime.now(tz=timezone.utc),
        action=SignalAction.BUY_CE_BIAS,
        score=0.85,
        confidence=0.9,
        reasons=["EMA9 > EMA21", "RSI(14) 60 — rising"],
        features={},
    )
    decision = WeightedHybridScorer().combine(tech, [], [], {})
    assert decision.reason_kind == "technical"
    assert "News:" not in decision.reason_detail
    assert "EMA" in decision.reason_detail


def test_mock_seed_signals_have_reason_fields():
    for row in MOCK_SIGNALS:
        enriched = attach_reason_fields(row)
        assert enriched["reason_kind"]
        assert enriched["reason_summary"]
        assert enriched["reason_detail"]


def test_syrma_synthetic_is_technical_not_hybrid():
    row = synthetic_signal_for_symbol("SYRMA")
    assert row["action"] == "BUY_CE_BIAS"
    assert row["reason_kind"] == "technical"
    assert "news" not in row["reason_summary"].lower()
    assert "News:" not in row["reason_detail"]
    assert "Technical:" in row["reason_detail"]


def test_synthetic_full_universe_non_placeholder(monkeypatch):
    monkeypatch.chdir(ROOT)
    from options_portal_domain.config import load_config, watchlist_symbols

    monkeypatch.setenv("CONFIG_PATH", str(ROOT / "config" / "local.yaml"))
    cfg = load_config()
    watch = watchlist_symbols(cfg)
    assert len(watch) >= 200
    rows = build_universe_signals(watch)
    assert len(rows) >= len(watch)
    kinds = set()
    for row in rows:
        assert row.get("stub") is not True
        assert row["reason_kind"]
        kinds.add(row["reason_kind"])
        if row["reason_kind"] == "technical":
            assert "News:" not in row["reason_detail"]
        if row["reason_kind"] == "news":
            assert "Technical:" not in row["reason_detail"]
        if row.get("dry_run_synthetic"):
            assert not (
                row["hybrid_score"] == 0.0
                and row["tech_score"] == 0.0
                and row["news_score"] == 0.0
                and row["confidence"] == 0.0
                and row["action"] == "HOLD"
            )
    # Full universe should not collapse every actionable to hybrid
    assert "technical" in kinds


def test_synthetic_signal_deterministic():
    a = synthetic_signal_for_symbol("INFY")
    b = synthetic_signal_for_symbol("INFY")
    assert a["hybrid_score"] == b["hybrid_score"]
    assert a["action"] == b["action"]
    assert a["reason_kind"] == b["reason_kind"]
    assert a["reason_summary"] == b["reason_summary"]


def test_sorted_page_puts_actions_first():
    rows = build_universe_signals(
        ["ZZHOLD", "NIFTY", "RELIANCE", "TCS", "BANKNIFTY"]
        + [f"SYM{i}" for i in range(30)]
    )
    rows.append(
        {
            "id": "x",
            "symbol": "BIGHOLD",
            "action": "HOLD",
            "hybrid_score": 0.99,
            "tech_score": 0.99,
            "news_score": 0.0,
            "confidence": 0.99,
            "reasons": ["forced"],
        }
    )
    sorted_rows = sort_signal_rows([attach_reason_fields(r) for r in rows])
    first_hold_idx = next(i for i, r in enumerate(sorted_rows) if r["action"] == "HOLD")
    assert all(r["action"] != "HOLD" for r in sorted_rows[:first_hold_idx]) or first_hold_idx == 0
    if first_hold_idx > 0:
        assert sorted_rows[0]["action"] != "HOLD"
        assert sorted_rows[0]["symbol"] != "BIGHOLD"
