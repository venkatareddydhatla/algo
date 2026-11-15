"""Top CE / PE hybrid strip selection."""

from __future__ import annotations

from options_portal_hybrid.explain import pick_top_hybrid_sides


def test_prefer_hybrid_kind_over_stronger_technical():
    rows = [
        {
            "symbol": "TECH_CE",
            "action": "BUY_CE_BIAS",
            "hybrid_score": 0.9,
            "confidence": 0.9,
            "reason_kind": "technical",
        },
        {
            "symbol": "HYB_CE",
            "action": "BUY_CE_BIAS",
            "hybrid_score": 0.55,
            "confidence": 0.7,
            "reason_kind": "hybrid",
        },
        {
            "symbol": "TECH_PE",
            "action": "BUY_PE_BIAS",
            "hybrid_score": -0.85,
            "confidence": 0.8,
            "reason_kind": "technical",
        },
        {
            "symbol": "HYB_PE",
            "action": "BUY_PE_BIAS",
            "hybrid_score": -0.5,
            "confidence": 0.6,
            "reason_kind": "hybrid",
        },
    ]
    sides = pick_top_hybrid_sides(rows, limit=2)
    assert sides["ce"][0]["symbol"] == "HYB_CE"
    assert sides["pe"][0]["symbol"] == "HYB_PE"


def test_fallback_to_strongest_actionable_abs_hybrid():
    rows = [
        {
            "symbol": "WEAK",
            "action": "BUY_CE_BIAS",
            "hybrid_score": 0.3,
            "confidence": 0.5,
            "reason_kind": "technical",
        },
        {
            "symbol": "STRONG",
            "action": "BUY_CE_BIAS",
            "hybrid_score": 0.8,
            "confidence": 0.4,
            "reason_kind": "technical",
        },
        {
            "symbol": "HOLD_POS",
            "action": "HOLD",
            "hybrid_score": 0.95,
            "confidence": 0.9,
            "reason_kind": "neutral",
        },
        {
            "symbol": "PE1",
            "action": "BUY_PE_BIAS",
            "hybrid_score": -0.4,
            "confidence": 0.5,
            "reason_kind": "news",
        },
    ]
    sides = pick_top_hybrid_sides(rows, limit=3)
    assert sides["ce"][0]["symbol"] == "STRONG"
    # HOLD still allowed as fallback after actionable ranks
    assert [r["symbol"] for r in sides["ce"]] == ["STRONG", "WEAK", "HOLD_POS"]
    assert sides["pe"][0]["symbol"] == "PE1"


def test_sides_split_by_sign():
    rows = [
        {"symbol": "A", "action": "BUY_CE_BIAS", "hybrid_score": 0.6, "confidence": 0.5, "reason_kind": "hybrid"},
        {"symbol": "B", "action": "BUY_PE_BIAS", "hybrid_score": -0.6, "confidence": 0.5, "reason_kind": "hybrid"},
        {"symbol": "C", "action": "HOLD", "hybrid_score": 0.0, "confidence": 0.1, "reason_kind": "neutral"},
    ]
    sides = pick_top_hybrid_sides(rows, limit=5)
    assert all(r["hybrid_score"] > 0 for r in sides["ce"])
    assert all(r["hybrid_score"] < 0 for r in sides["pe"])
    assert "C" not in {r["symbol"] for r in sides["ce"] + sides["pe"]}
