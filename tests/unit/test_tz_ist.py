"""IST / tzdata resolution (critical on Windows without system zoneinfo)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from options_portal_domain.tz import (
    calendar_quarter_bounds,
    current_quarter_start,
    ensure_tzdata,
    format_ist_display,
    ist,
    ist_day_start,
    now_ist,
)


def test_ensure_tzdata_and_ist_resolves():
    ensure_tzdata()
    tz = ist()
    # Prefer real IANA key when tzdata (or system zones) are present
    assert tz is not None
    now = datetime.now(tz=tz)
    assert now.utcoffset() == timedelta(hours=5, minutes=30)


def test_now_ist_aware():
    dt = now_ist()
    assert dt.tzinfo is not None
    assert dt.utcoffset() == timedelta(hours=5, minutes=30)


def test_format_ist_display_includes_date_and_time():
    """Signals Updated (and similar) must show date + time, not time-only."""
    utc = datetime(2026, 11, 15, 4, 5, tzinfo=timezone.utc)  # 09:35 IST
    display = format_ist_display(utc)
    assert display == "2026-11-15 09:35 IST"
    assert "2026-11-15" in display
    assert display.endswith("IST")


def test_ist_day_start_and_quarter_bounds():
    start = ist_day_start(date(2026, 11, 15))
    assert start.hour == 0 and start.minute == 0
    assert start.utcoffset() == timedelta(hours=5, minutes=30)
    q_start, q_end = calendar_quarter_bounds(date(2026, 11, 15))
    assert q_start == date(2026, 10, 1)
    assert q_end == date(2026, 12, 31)
    assert current_quarter_start(date(2026, 4, 2)) == date(2026, 4, 1)


def test_asia_kolkata_zoneinfo_after_tzdata():
    ensure_tzdata()
    # Same key that previously crashed Windows API import of backtest.candles
    z = ZoneInfo("Asia/Kolkata")
    assert datetime(2024, 6, 1, 9, 15, tzinfo=z).utcoffset() == timedelta(
        hours=5, minutes=30
    )


def test_calendar_ist_import_safe():
    from options_portal_domain.calendar import IST, IndiaMarketCalendar

    probe = datetime(2024, 1, 15, 12, 0, tzinfo=IST)
    assert probe.utcoffset() == timedelta(hours=5, minutes=30)
    cal = IndiaMarketCalendar()
    open_dt, close_dt = cal.session_open_close_ist(datetime.now(tz=timezone.utc).date())
    assert open_dt.tzinfo is not None
    assert close_dt > open_dt
