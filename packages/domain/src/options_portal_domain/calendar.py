"""India NSE market calendar helpers (expiry-aware; holiday list stub)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from options_portal_domain.tz import ist, now_ist

# Lazy-safe IST (tzdata on Windows; fixed +05:30 fallback)
IST = ist()

# Minimal holiday stub — replace with full NSE circular calendar.
# TODO: load holidays from config/symbols.india.yaml or an exchange feed.
# Incomplete list: weekday Mon–Fri is always required; expand holidays as follow-up.
NSE_HOLIDAYS_2026: set[date] = {
    date(2026, 1, 26),  # Republic Day
    date(2026, 3, 3),  # Holi (approx — verify)
    date(2026, 8, 15),  # Independence Day
    date(2026, 10, 2),  # Gandhi Jayanti
    date(2026, 11, 8),  # Diwali (approx — verify)
}

INDEX_WEEKLY_SYMBOLS = {
    "NIFTY",
    "BANKNIFTY",
    "FINNIFTY",
    "MIDCPNIFTY",
    "NIFTYNXT50",
    "NIFTYFPI",
}


class IndiaMarketCalendar:
    """NSE session + F&O expiry conventions (config-overridable)."""

    def __init__(
        self,
        *,
        session_open: str = "09:15",
        session_close: str = "15:30",
        holidays: set[date] | None = None,
    ) -> None:
        self._open = _parse_hhmm(session_open)
        self._close = _parse_hhmm(session_close)
        self._holidays = holidays if holidays is not None else set(NSE_HOLIDAYS_2026)

    def is_trading_day(self, d: date) -> bool:
        if d.weekday() >= 5:
            return False
        return d not in self._holidays

    def session_open_close_ist(self, d: date) -> tuple[datetime, datetime]:
        open_dt = datetime.combine(d, self._open, tzinfo=IST)
        close_dt = datetime.combine(d, self._close, tzinfo=IST)
        return open_dt, close_dt

    def next_weekly_expiry(self, symbol: str, as_of: date) -> date:
        """Nearest upcoming Thursday that is a trading day (index weeklies).

        NSE may shift weeklies on holidays — callers should allow config override.
        """
        _ = symbol  # symbol reserved for per-index rules
        d = as_of
        for _ in range(14):
            # Thursday == 3
            if d.weekday() == 3 and self.is_trading_day(d) and d >= as_of:
                return d
            d += timedelta(days=1)
        # Fallback: next Thursday even if holiday (caller should adjust)
        d = as_of
        while d.weekday() != 3:
            d += timedelta(days=1)
        return d

    def next_monthly_expiry(self, symbol: str, as_of: date) -> date:
        """Last Thursday of the month that is a trading day (stock F&O)."""
        _ = symbol
        year, month = as_of.year, as_of.month
        # Walk from end of month backward to last Thursday trading day
        if month == 12:
            last = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            last = date(year, month + 1, 1) - timedelta(days=1)
        d = last
        while d.weekday() != 3:
            d -= timedelta(days=1)
        while not self.is_trading_day(d):
            d -= timedelta(days=1)
        if d < as_of:
            # roll to next month
            if month == 12:
                return self.next_monthly_expiry(symbol, date(year + 1, 1, 1))
            return self.next_monthly_expiry(symbol, date(year, month + 1, 1))
        return d

    def pick_expiry(self, symbol: str, as_of: date | None = None) -> date:
        as_of = as_of or datetime.now(tz=ist()).date()
        if symbol.upper() in INDEX_WEEKLY_SYMBOLS:
            return self.next_weekly_expiry(symbol, as_of)
        return self.next_monthly_expiry(symbol, as_of)


def is_nse_trading_day(
    d: date | None = None,
    *,
    holidays: set[date] | None = None,
) -> bool:
    """True for Mon–Fri IST days that are not in the NSE holiday stub.

    ``d`` defaults to today's IST calendar date. Pass ``holidays`` to override
    the stub (empty set = weekends only). Full NSE circular coverage is a
    follow-up — expand ``NSE_HOLIDAYS_2026`` or load from config/feed.
    """
    day = d if d is not None else now_ist().date()
    if holidays is not None:
        return IndiaMarketCalendar(holidays=holidays).is_trading_day(day)
    return IndiaMarketCalendar().is_trading_day(day)


def previous_nse_trading_day(
    d: date,
    *,
    holidays: set[date] | None = None,
) -> date:
    """Nearest NSE trading day strictly before ``d``."""
    cal = (
        IndiaMarketCalendar(holidays=holidays)
        if holidays is not None
        else IndiaMarketCalendar()
    )
    cursor = d - timedelta(days=1)
    for _ in range(14):
        if cal.is_trading_day(cursor):
            return cursor
        cursor -= timedelta(days=1)
    return cursor


def pnl_ist_day(
    as_of: datetime | None = None,
    *,
    holidays: set[date] | None = None,
) -> date:
    """IST date used for Day realized / day-loss (NSE cash session day).

    Calendar midnight IST is the wrong boundary for Trades UI: after 00:00 IST
    Closed trades still list the prior session's round-trips, but a naive
    ``now_ist().date()`` sum goes to ₹0. Attribute PnL to the session day
    from cash open (09:15 IST) through the next trading morning before open;
    before open (and on weekends/holidays) use the previous NSE trading day.
    """
    now = as_of if as_of is not None else now_ist()
    if now.tzinfo is None:
        now = now.replace(tzinfo=ist())
    else:
        now = now.astimezone(ist())
    cal = (
        IndiaMarketCalendar(holidays=holidays)
        if holidays is not None
        else IndiaMarketCalendar()
    )
    today = now.date()
    open_dt, _ = cal.session_open_close_ist(today)
    if cal.is_trading_day(today) and now >= open_dt:
        return today
    return previous_nse_trading_day(today, holidays=holidays)


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))
