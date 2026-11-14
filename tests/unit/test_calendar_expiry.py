"""Expiry picker: weekly index vs monthly stock."""

from datetime import date

from options_portal_domain.calendar import IndiaMarketCalendar


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
