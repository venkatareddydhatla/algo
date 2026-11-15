"""Dry-run synthetic signals prefer live RSS headlines when present."""

from __future__ import annotations

from app.mock_data import synthetic_signal_for_symbol


def test_synthetic_uses_live_headlines_not_dry_run_filler():
    live = [
        {
            "id": "x",
            "score": 0.62,
            "confidence": 0.8,
            "title": "Foreign inflows lift Reliance into close",
            "symbols": ["RELIANCE"],
        }
    ]
    row = synthetic_signal_for_symbol("RELIANCE", live_news=live)
    assert row["live_news"] is True
    assert row["news_score"] == 0.62
    joined = " ".join(row["reasons"])
    assert "Foreign inflows lift Reliance" in joined
    assert "Dry-run" not in joined


def test_synthetic_without_live_may_use_dry_run_filler():
    row = synthetic_signal_for_symbol("ZZTESTSYM")
    assert row.get("live_news") is False
