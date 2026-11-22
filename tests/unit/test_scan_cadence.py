"""Unit tests for 15m bar-close full-list scan cadence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from options_portal_domain.config import load_config, scan_symbols
from options_portal_domain.entry_selection import entry_selection_config
from options_portal_domain.pine_signals import (
    floor_to_timeframe,
    latest_closed_bar_open,
)
from options_portal_domain.scan_cadence import (
    bar_close_scan_ready,
    next_bar_close_at,
    scan_cadence_config,
    seconds_until_bar_close_scan,
)


def _utc(y: int, m: int, d: int, hh: int, mm: int, ss: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, ss, tzinfo=timezone.utc)


def test_next_bar_close_mid_bucket():
    # IST 10:07 → UTC 04:37; forming 15m ends UTC 04:45.
    now = _utc(2026, 11, 20, 4, 37, 12)
    assert next_bar_close_at(now, timeframe_minutes=15) == _utc(2026, 11, 20, 4, 45)


def test_next_bar_close_on_boundary_is_now():
    now = _utc(2026, 11, 20, 4, 45, 0)
    assert next_bar_close_at(now, timeframe_minutes=15) == now


def test_first_scan_immediate_catchup():
    now = _utc(2026, 11, 20, 4, 37, 0)
    assert (
        seconds_until_bar_close_scan(
            now, timeframe_minutes=15, settle_sec=8.0, last_closed_scanned=None
        )
        == 0.0
    )
    assert bar_close_scan_ready(
        now, timeframe_minutes=15, settle_sec=8.0, last_closed_scanned=None
    )


def test_wait_until_next_close_after_scanning_same_bar():
    # Mid-bar after scanning the prior closed open.
    now = _utc(2026, 11, 20, 4, 37, 0)
    closed = latest_closed_bar_open(now, timeframe_minutes=15)
    assert closed == _utc(2026, 11, 20, 4, 15)
    wait_s = seconds_until_bar_close_scan(
        now,
        timeframe_minutes=15,
        settle_sec=8.0,
        last_closed_scanned=closed,
    )
    # Next close 04:45 + 8s settle from 04:37 → 8m + 8s = 488s.
    assert abs(wait_s - (8 * 60 + 8)) < 0.01
    assert not bar_close_scan_ready(
        now,
        timeframe_minutes=15,
        settle_sec=8.0,
        last_closed_scanned=closed,
    )


def test_ready_after_new_bar_close_past_settle():
    # 04:45:10 — 04:30–04:45 just closed; settle 8s → ready.
    now = _utc(2026, 11, 20, 4, 45, 10)
    prev_closed = _utc(2026, 11, 20, 4, 15)  # previously scanned
    closed = latest_closed_bar_open(now, timeframe_minutes=15)
    assert closed == _utc(2026, 11, 20, 4, 30)
    wait_s = seconds_until_bar_close_scan(
        now,
        timeframe_minutes=15,
        settle_sec=8.0,
        last_closed_scanned=prev_closed,
    )
    assert wait_s == 0.0
    assert bar_close_scan_ready(
        now,
        timeframe_minutes=15,
        settle_sec=8.0,
        last_closed_scanned=prev_closed,
    )


def test_settle_delay_right_after_boundary():
    now = _utc(2026, 11, 20, 4, 45, 3)
    prev_closed = _utc(2026, 11, 20, 4, 15)
    wait_s = seconds_until_bar_close_scan(
        now,
        timeframe_minutes=15,
        settle_sec=8.0,
        last_closed_scanned=prev_closed,
    )
    assert abs(wait_s - 5.0) < 0.01


def test_scan_cadence_config_defaults_on_bar_close(monkeypatch):
    monkeypatch.delenv("WORKER_SCAN_ON_BAR_CLOSE", raising=False)
    monkeypatch.delenv("WORKER_BAR_CLOSE_SETTLE_SEC", raising=False)
    monkeypatch.delenv("WORKER_INTERVAL_SEC", raising=False)
    cfg = scan_cadence_config({"trading": {"timeframe": "15m"}})
    assert cfg["on_bar_close"] is True
    assert cfg["timeframe_minutes"] == 15
    assert cfg["settle_sec"] == 8.0
    assert cfg["fallback_interval_sec"] == 60


def test_scan_cadence_env_can_disable_bar_close(monkeypatch):
    monkeypatch.setenv("WORKER_SCAN_ON_BAR_CLOSE", "0")
    monkeypatch.setenv("WORKER_INTERVAL_SEC", "90")
    cfg = scan_cadence_config({"trading": {"scan_on_bar_close": True}})
    assert cfg["on_bar_close"] is False
    assert cfg["fallback_interval_sec"] == 90


def test_local_yaml_full_volatile_list_and_bar_close_no_cooloff():
    """Delivery invariants: full list each pass, bar-close on, cooloff off."""
    cfg = load_config("config/local.yaml")
    trading = cfg["trading"]
    assert trading.get("scan_on_bar_close") is True
    assert float(trading.get("bar_close_settle_sec", 0)) >= 0
    assert int(trading.get("max_active_symbols") or 0) == 0
    active = scan_symbols(cfg)
    watch = trading.get("symbols_resolved") or []
    assert len(active) == len(watch)
    assert len(active) == 19  # TradingView curated list (customs may raise total at runtime)
    assert active == [str(s).upper() for s in watch]
    cadence = scan_cadence_config(cfg)
    assert cadence["on_bar_close"] is True
    assert cadence["timeframe_minutes"] == 15
    sel = entry_selection_config(cfg)
    cool = sel["reentry_cooloff"]
    assert cool["enabled"] is False
    assert int(cool["minutes"] or 0) == 0


def test_floor_aligns_with_latest_closed_helper():
    now = _utc(2026, 11, 20, 4, 44, 59)
    bucket = floor_to_timeframe(now, timeframe_minutes=15)
    assert bucket == _utc(2026, 11, 20, 4, 30)
    assert latest_closed_bar_open(now, timeframe_minutes=15) == bucket - timedelta(
        minutes=15
    )
