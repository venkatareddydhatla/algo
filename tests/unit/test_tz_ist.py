"""IST / tzdata resolution (critical on Windows without system zoneinfo)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from options_portal_domain.tz import ensure_tzdata, ist, now_ist


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
