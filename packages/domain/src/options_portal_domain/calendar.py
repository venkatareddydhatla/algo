"""India NSE market calendar helpers (expiry-aware; holiday list stub)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# Minimal holiday stub — replace with full NSE circular calendar.
# TODO: load holidays from config/symbols.india.yaml or an exchange feed.
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
        as_of = as_of or datetime.now(tz=IST).date()
        if symbol.upper() in INDEX_WEEKLY_SYMBOLS:
            return self.next_weekly_expiry(symbol, as_of)
        return self.next_monthly_expiry(symbol, as_of)


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))
