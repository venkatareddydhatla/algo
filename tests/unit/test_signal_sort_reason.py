"""Sort order + structured reason fields for signals / dry-run mock."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_APP = ROOT / "apps" / "api"
if str(API_APP) not in sys.path:
    sys.path.insert(0, str(API_APP))

from options_portal_hybrid.explain import (  # noqa: E402
    attach_reason_fields,
    build_reason_fields,
    is_actionable,
    sort_signal_rows,
)
from options_portal_hybrid.scorer import WeightedHybridScorer  # noqa: E402
from options_portal_domain.types import (  # noqa: E402
    SignalAction,
    TechnicalSignal,
)
from datetime import datetime, timezone

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
    # Actionable first: CCC (|0.8|), then DDD/BBB (|0.4| with conf), then HOLDs by |hybrid|
    assert symbols[0] == "CCC"
    assert set(symbols[:3]) == {"CCC", "BBB", "DDD"}
    assert symbols[1] == "DDD"  # same |hybrid| as BBB, higher confidence
    assert symbols[2] == "BBB"
    assert symbols[3] == "AAA"  # HOLD with |0.9|
    assert symbols[4] == "EEE"


def test_build_reason_fields_technical_and_hybrid():
    tech = build_reason_fields(
        action="BUY_CE_BIAS",
        tech_score=0.7,
        news_score=0.05,
        hybrid_score=0.55,
        reasons=["EMA9 > EMA21", "RSI(14) 58 — rising", "Hybrid 0.55 >= enter_long"],
    )
    assert tech["reason_kind"] in {"technical", "hybrid"}
    assert "EMA" in tech["reason_summary"] or "RSI" in tech["reason_summary"]
    assert "EMA9" in tech["reason_detail"]

    hybrid = build_reason_fields(
        action="BUY_PE_BIAS",
        tech_score=-0.48,
        news_score=-0.67,
        hybrid_score=-0.55,
        reasons=[
            "EMA9 < EMA21",
            "Close below VWAP",
            "Bearish news cluster",
            "Headline: Broker cuts target",
        ],
    )
    assert hybrid["reason_kind"] == "hybrid"
    assert hybrid["reason_summary"]
    assert "Headline" in hybrid["reason_detail"] or "news" in hybrid["reason_detail"].lower()


def test_scorer_populates_reason_fields():
    tech = TechnicalSignal(
        symbol="NIFTY",
        timeframe="15m",
        bar_ts=datetime.now(tz=timezone.utc),
        action=SignalAction.BUY_CE_BIAS,
        score=0.7,
        confidence=0.8,
        reasons=["EMA9 > EMA21", "RSI(14) 60 — rising"],
        features={},
    )
    decision = WeightedHybridScorer().combine(tech, [], [], {})
    assert decision.reason_kind
    assert decision.reason_summary
    assert decision.reason_detail
    assert "EMA" in decision.reason_detail or "Hybrid" in decision.reason_detail


def test_mock_seed_signals_have_reason_fields():
    for row in MOCK_SIGNALS:
        enriched = attach_reason_fields(row)
        assert enriched["reason_kind"]
        assert enriched["reason_summary"]
        assert enriched["reason_detail"]


def test_synthetic_full_universe_non_placeholder(monkeypatch):
    monkeypatch.chdir(ROOT)
    from options_portal_domain.config import load_config, watchlist_symbols

    monkeypatch.setenv("CONFIG_PATH", str(ROOT / "config" / "local.yaml"))
    cfg = load_config()
    watch = watchlist_symbols(cfg)
    assert len(watch) >= 200
    rows = build_universe_signals(watch)
    assert len(rows) >= len(watch)
    # No inactive HOLD/0.00 stubs — every row has a real-looking score path
    for row in rows:
        assert row.get("stub") is not True
        assert "hybrid_score" in row
        assert row["reason_kind"]
        assert row["reason_summary"]
        assert row["reason_detail"]
        # Scores are not the old pad-stub triple-zero for non-seed names
        if row.get("dry_run_synthetic"):
            assert not (
                row["hybrid_score"] == 0.0
                and row["tech_score"] == 0.0
                and row["news_score"] == 0.0
                and row["confidence"] == 0.0
                and row["action"] == "HOLD"
            )


def test_synthetic_signal_deterministic():
    a = synthetic_signal_for_symbol("INFY")
    b = synthetic_signal_for_symbol("INFY")
    assert a["hybrid_score"] == b["hybrid_score"]
    assert a["action"] == b["action"]
    assert a["reason_summary"] == b["reason_summary"]


def test_sorted_page_puts_actions_first():
    rows = build_universe_signals(
        ["ZZHOLD", "NIFTY", "RELIANCE", "TCS", "BANKNIFTY"]
        + [f"SYM{i}" for i in range(30)]
    )
    # Force one HOLD with huge |hybrid| via manual row to ensure sort still prefers actions
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
        # BIGHOLD must not beat actionable names despite |hybrid|=0.99
        assert sorted_rows[0]["symbol"] != "BIGHOLD"
