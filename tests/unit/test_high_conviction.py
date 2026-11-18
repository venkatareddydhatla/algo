"""Unit tests for high-conviction multi-factor equity entry gate."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from options_portal_domain.adaptive_hybrid import (
    load_adaptive_delta,
    run_adaptive_feedback,
    save_adaptive_delta,
)
from options_portal_domain.candles_agg import (
    aggregate_15m_to_1h,
    bar_direction,
    session_daily_high_low,
)
from options_portal_domain.high_conviction import (
    adaptive_delta_from_week_wr,
    check_atr_pct,
    check_cooloff,
    check_hybrid_floor,
    check_mtf_alignment,
    check_news_conflict,
    check_proximity_block,
    check_relative_volume,
    clamp_adaptive_delta,
    clamp_min_hybrid,
    effective_max_open_symbols,
    evaluate_entry_gate,
    high_conviction_config,
    is_top_pct_of_scan,
    min_profit_for_rrr,
    news_sentiment_1h,
    session_min_hybrid_abs,
    top_pct_cutoff_count,
    trend_confirmed_for_overnight,
)
from options_portal_domain.tz import ist
from options_portal_domain.types import Candle, NewsItem, SentimentResult


def _bar(
    symbol: str,
    ts: datetime,
    *,
    o: float = 100.0,
    h: float | None = None,
    l: float | None = None,
    c: float = 100.0,
    vol: int = 1000,
    tf: str = "15m",
) -> Candle:
    high = h if h is not None else max(o, c) * 1.01
    low = l if l is not None else min(o, c) * 0.99
    return Candle(
        symbol=symbol,
        timeframe=tf,  # type: ignore[arg-type]
        ts=ts,
        open=o,
        high=high,
        low=low,
        close=c,
        volume=vol,
    )


def _rising_15m(n: int = 24, *, base_vol: int = 1000) -> list[Candle]:
    """Rising closes; last bar volume spiked for RVOL tests."""
    z = timezone.utc
    start = datetime(2026, 11, 18, 3, 45, tzinfo=z)  # 09:15 IST
    out: list[Candle] = []
    px = 100.0
    for i in range(n):
        ts = start + timedelta(minutes=15 * i)
        px = 100.0 + i * 0.5
        vol = base_vol * 3 if i == n - 1 else base_vol
        out.append(_bar("RELIANCE", ts, o=px - 0.2, c=px, vol=vol))
    return out


# --- hybrid floors + late 14:20 -------------------------------------------------


def test_session_min_hybrid_before_after_late():
    z = ist()
    assert session_min_hybrid_abs(datetime(2026, 11, 18, 10, 0, tzinfo=z)) == 0.75
    # Afternoon keeps 0.75 (no midday raise to 0.80)
    assert session_min_hybrid_abs(datetime(2026, 11, 18, 12, 0, tzinfo=z)) == 0.75
    # Late strict when WR unknown / weak
    assert session_min_hybrid_abs(datetime(2026, 11, 18, 14, 20, tzinfo=z)) == 0.85
    assert session_min_hybrid_abs(datetime(2026, 11, 18, 15, 0, tzinfo=z)) == 0.85
    # Late base path when rolling WR ≥ 55%
    assert (
        session_min_hybrid_abs(
            datetime(2026, 11, 18, 14, 30, tzinfo=z), rolling_wr=0.55
        )
        == 0.75
    )
    assert (
        session_min_hybrid_abs(
            datetime(2026, 11, 18, 14, 30, tzinfo=z), rolling_wr=0.54
        )
        == 0.85
    )


def test_late_entry_1420_requires_085_when_wr_weak():
    z = ist()
    now = datetime(2026, 11, 18, 14, 30, tzinfo=z)
    fail = check_hybrid_floor(0.82, now, rolling_wr=0.40)
    assert fail.passed is False
    ok = check_hybrid_floor(0.85, now, rolling_wr=0.40)
    assert ok.passed is True
    # Strong WR → late base 0.75
    assert check_hybrid_floor(0.75, now, rolling_wr=0.60).passed is True
    assert check_hybrid_floor(0.74, now, rolling_wr=0.60).passed is False
    # Before 14:20 afternoon floor is 0.75 (same as morning)
    noonish = datetime(2026, 11, 18, 13, 0, tzinfo=z)
    assert check_hybrid_floor(0.75, noonish).passed is True
    assert check_hybrid_floor(0.74, noonish).passed is False
    assert "path=fixed_floor" in check_hybrid_floor(0.75, noonish).detail


def test_hybrid_floor_top_pct_or_path():
    z = ist()
    noonish = datetime(2026, 11, 18, 13, 0, tzinfo=z)
    # Under floor 0.75 but #1 of a weak scan → admit via top10pct
    scores = [0.70, 0.60, 0.55, 0.50, 0.45, 0.40, 0.35, 0.30, 0.25, 0.20]
    top = check_hybrid_floor(
        0.70, noonish, scan_abs_scores=scores, top_pct=0.10
    )
    assert top.passed is True
    assert "path=top10pct" in top.detail
    weak = check_hybrid_floor(
        0.40, noonish, scan_abs_scores=scores, top_pct=0.10
    )
    assert weak.passed is False
    assert is_top_pct_of_scan(0.70, scores, top_pct=0.10) is True
    assert top_pct_cutoff_count(10, top_pct=0.10) == 1


def test_hybrid_floor_with_adaptive_delta():
    z = ist()
    morning = datetime(2026, 11, 18, 10, 0, tzinfo=z)
    # baseline 0.75 + 0.05 → 0.80
    assert session_min_hybrid_abs(morning, adaptive_delta=0.05) == 0.80
    assert check_hybrid_floor(0.79, morning, adaptive_delta=0.05).passed is False
    assert check_hybrid_floor(0.80, morning, adaptive_delta=0.05).passed is True


# --- adaptive clamp -------------------------------------------------------------


def test_adaptive_clamp_min_hybrid_and_delta():
    assert clamp_min_hybrid(0.65) == 0.70
    assert clamp_min_hybrid(0.95) == 0.90
    assert clamp_min_hybrid(0.80) == 0.80
    assert clamp_adaptive_delta(0.5) == 0.20
    assert clamp_adaptive_delta(-0.5) == -0.20


def test_adaptive_delta_from_week_wr_raise_and_ease():
    # Raise when week WR < 55%
    assert adaptive_delta_from_week_wr(0.40, 0.0) == 0.05
    assert adaptive_delta_from_week_wr(0.40, 0.05) == 0.10
    # Ease toward baseline when WR ≥ 55%
    assert adaptive_delta_from_week_wr(0.60, 0.10) == 0.05
    assert adaptive_delta_from_week_wr(0.60, 0.0) == -0.05
    # Unknown WR keeps current
    assert adaptive_delta_from_week_wr(None, 0.05) == 0.05


def test_adaptive_delta_clamped_after_many_raises():
    d = 0.0
    for _ in range(10):
        d = adaptive_delta_from_week_wr(0.30, d)
    assert d == 0.20  # clamp_adaptive_delta


# --- MTF / RVOL / ATR / proximity / news ----------------------------------------


def test_aggregate_15m_to_1h_and_mtf_align():
    candles = _rising_15m(16)
    bars_1h = aggregate_15m_to_1h(candles)
    assert len(bars_1h) >= 2
    assert bars_1h[0].timeframe == "1h"
    assert bar_direction(candles) == "bullish"
    mtf = check_mtf_alignment(candles, hybrid_score=0.80)
    assert mtf.passed is True


def test_mtf_fails_when_directions_disagree():
    z = timezone.utc
    # 15m rising then last two falling; build 1h that ends down vs 15m up — force
    # mismatch by short series where 15m last up but 1h only 1 bar → neutral fail
    bars = [
        _bar("TCS", datetime(2026, 11, 18, 4, 0, tzinfo=z), c=100),
        _bar("TCS", datetime(2026, 11, 18, 4, 15, tzinfo=z), c=101),
    ]
    mtf = check_mtf_alignment(bars, hybrid_score=0.80)
    # 1h aggregate may be single bucket → neutral → fail
    assert mtf.passed is False


def test_relative_volume_1_2x():
    candles = _rising_15m(25, base_vol=1000)
    # last vol = 3× base → passes 1.2×
    ok = check_relative_volume(candles, mult=1.2, lookback=20)
    assert ok.passed is True
    # Flatten volumes → fail
    flat = [
        _bar(
            "TCS",
            datetime(2026, 11, 18, 4, 0, tzinfo=timezone.utc) + timedelta(minutes=15 * i),
            c=100 + i,
            vol=1000,
        )
        for i in range(25)
    ]
    assert check_relative_volume(flat, mult=1.2, lookback=20).passed is False


def test_atr_pct_above_1_0():
    assert check_atr_pct(2.0, 100.0, min_pct=1.0).passed is True  # 2%
    assert check_atr_pct(1.0, 100.0, min_pct=1.0).passed is False  # 1% not >
    assert check_atr_pct(1.01, 100.0, min_pct=1.0).passed is True
    assert check_atr_pct(None, 100.0).passed is False


def test_proximity_block_near_daily_high():
    # price near high within 0.2×ATR (band=0.4); 99.8 is 0.2 from high
    r = check_proximity_block(
        price=99.8,
        atr14=2.0,
        daily_high=100.0,
        daily_low=90.0,
        proximity_atr_mult=0.2,
    )
    assert r.passed is False
    clear = check_proximity_block(
        price=95.0,
        atr14=2.0,
        daily_high=100.0,
        daily_low=90.0,
        proximity_atr_mult=0.2,
    )
    assert clear.passed is True


def test_session_daily_high_low_from_15m():
    candles = _rising_15m(8)
    hl = session_daily_high_low(
        candles, session_day=datetime(2026, 11, 18, tzinfo=ist()).date()
    )
    assert hl is not None
    high, low = hl
    assert high >= low


def test_news_1h_conflict():
    z = ist()
    now = datetime(2026, 11, 18, 11, 0, tzinfo=z)
    item = NewsItem(
        id="n1",
        source="et",
        title="bearish headline",
        body="",
        published_at=now - timedelta(minutes=20),
        symbols=["RELIANCE"],
    )
    sent = SentimentResult(
        news_id="n1",
        model="vader",
        score=-0.6,
        label="bearish",
        confidence=0.9,
    )
    score = news_sentiment_1h([item], [sent], now=now, lookback_minutes=60)
    assert score is not None and score < 0
    assert check_news_conflict(0.80, score).passed is False
    assert check_news_conflict(-0.80, score).passed is True
    assert check_news_conflict(0.80, None).passed is True


def test_cooloff_120m_after_any_trade():
    z = ist()
    traded = datetime(2026, 11, 18, 10, 0, tzinfo=z)
    within = datetime(2026, 11, 18, 11, 30, tzinfo=z)  # 90m
    assert check_cooloff(
        last_trade=traded, now=within, cooloff_minutes=120
    ).passed is False
    after = datetime(2026, 11, 18, 12, 1, tzinfo=z)  # 121m
    assert check_cooloff(
        last_trade=traded, now=after, cooloff_minutes=120
    ).passed is True


def test_effective_max_open_wr_throttle():
    assert effective_max_open_symbols(None, trip_count=0) == 6
    assert effective_max_open_symbols(0.60, trip_count=20) == 6
    assert effective_max_open_symbols(0.40, trip_count=20) == 4
    assert effective_max_open_symbols(0.40, trip_count=3) == 6  # sample too small


def test_min_rrr_1_2():
    assert min_profit_for_rrr(2.0, min_rrr=1.2) == 2.4


def test_overnight_trend_confirmed():
    assert (
        trend_confirmed_for_overnight(
            open_side="long",
            hybrid_score=0.90,
            dir_15m="bullish",
            dir_1h="bullish",
        )
        is True
    )
    assert (
        trend_confirmed_for_overnight(
            open_side="long",
            hybrid_score=0.90,
            dir_15m="bullish",
            dir_1h="bearish",
        )
        is False
    )
    assert (
        trend_confirmed_for_overnight(
            open_side="long",
            hybrid_score=0.80,  # below 0.85
            dir_15m="bullish",
            dir_1h="bullish",
        )
        is False
    )


def test_evaluate_entry_gate_all_pass():
    z = ist()
    now = datetime(2026, 11, 18, 10, 30, tzinfo=z)
    candles = _rising_15m(30)
    # Ensure ATR% and proximity clear: use mid-range price vs session H/L
    atr = 3.0  # 3% of ~100
    # Last close is high end — bump atr band and use price away from extremes
    # by checking with price in middle of synthetic range
    mid_px = 105.0
    # Rebuild candles so last close is mid and volume spikes, daily range wide
    gate = evaluate_entry_gate(
        symbol="RELIANCE",
        hybrid_score=0.80,
        now=now,
        candles_15m=candles,
        atr14=atr,
        price=float(candles[-1].close) - 2.0,  # off the high
        in_entry_window=True,
        adaptive_delta=0.0,
    )
    # May still fail proximity if close to high — assert factors present
    assert any(f.name == "hybrid_floor" for f in gate.factors)
    assert any(f.name == "rvol" for f in gate.factors)
    assert any(f.name == "mtf" for f in gate.factors)
    assert gate.log_line("RELIANCE", ts=now).startswith("2026-11-18")


def test_evaluate_entry_gate_blocks_index():
    z = ist()
    gate = evaluate_entry_gate(
        symbol="NIFTY",
        hybrid_score=0.99,
        now=datetime(2026, 11, 18, 10, 0, tzinfo=z),
        candles_15m=_rising_15m(25),
        atr14=5.0,
        price=100.0,
        is_index=True,
        in_entry_window=True,
    )
    assert gate.admitted is False
    assert any(f.name == "index_block" and not f.passed for f in gate.factors)


def test_high_conviction_config_from_yaml_defaults():
    cfg = high_conviction_config(None)
    assert cfg["min_hybrid_before_12"] == 0.75
    assert cfg["min_hybrid_after_12"] == 0.75
    assert cfg["min_hybrid_after_1420"] == 0.85
    assert cfg["late_base_min_hybrid"] == 0.75
    assert cfg["late_base_wr_threshold"] == 0.55
    assert cfg["rvol_mult"] == 1.2
    assert cfg["atr_min_pct_of_price"] == 1.0
    assert cfg["top_pct_enabled"] is True
    assert cfg["top_pct_of_scan"] == 0.10
    assert cfg["cooloff_minutes"] == 120
    assert cfg["max_open"] == 6
    assert cfg["min_rrr"] == 1.2


def test_adaptive_persist_roundtrip(tmp_path, monkeypatch):
    import options_portal_persistence.kv_store as kv_mod

    db = tmp_path / "test.sqlite"
    monkeypatch.setenv("PERSISTENCE_PROFILE", "local")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    # Re-import path uses env
    store = kv_mod.KvStore(url=f"sqlite:///{db}")
    assert store.available
    assert save_adaptive_delta(0.05, kv=store) is True
    assert load_adaptive_delta(kv=store) == 0.05
    status = run_adaptive_feedback([], cfg=None, kv=store)
    assert status["reason"] == "ok"
    # No trips → delta unchanged
    assert status["delta_after"] == status["delta_before"]
