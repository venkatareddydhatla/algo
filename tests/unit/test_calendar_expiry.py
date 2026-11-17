"""Expiry picker + NSE trading-day helpers."""

from datetime import date

from options_portal_domain.calendar import IndiaMarketCalendar, is_nse_trading_day


def test_nifty_weekly_expiry_is_thursday_on_or_after():
    cal = IndiaMarketCalendar(holidays=set())
    as_of = date(2026, 11, 16)  # Monday
    exp = cal.next_weekly_expiry("NIFTY", as_of)
    assert exp.weekday() == 3
    assert exp >= as_of


def test_reliance_monthly_expiry_last_thursday():
    cal = IndiaMarketCalendar(holidays=set())
    as_of = date(2026, 11, 3)
    exp = cal.next_monthly_expiry("RELIANCE", as_of)
    assert exp.weekday() == 3
    assert exp.month == 11
    assert exp >= as_of


def test_pick_expiry_routes_index_vs_stock():
    cal = IndiaMarketCalendar(holidays=set())
    as_of = date(2026, 11, 10)
    weekly = cal.pick_expiry("BANKNIFTY", as_of)
    monthly = cal.pick_expiry("TCS", as_of)
    assert weekly <= monthly or weekly.month == monthly.month
    assert weekly.weekday() == 3
    assert monthly.weekday() == 3


def test_is_trading_day_skips_weekend_and_holiday_stub():
    cal = IndiaMarketCalendar()
    assert cal.is_trading_day(date(2026, 11, 16)) is True  # Monday
    assert cal.is_trading_day(date(2026, 11, 14)) is False  # Saturday
    assert cal.is_trading_day(date(2026, 11, 15)) is False  # Sunday
    assert cal.is_trading_day(date(2026, 1, 26)) is False  # Republic Day stub
    assert is_nse_trading_day(date(2026, 11, 15)) is False
    assert is_nse_trading_day(date(2026, 11, 16)) is True
    # weekends-only when holidays overridden empty
    assert is_nse_trading_day(date(2026, 1, 26), holidays=set()) is True
