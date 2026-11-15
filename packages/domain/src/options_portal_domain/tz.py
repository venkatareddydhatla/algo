"""IANA timezone helpers — ensure tzdata on Windows (no system zoneinfo)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone, tzinfo
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_IST_KEY = "Asia/Kolkata"
_IST_FIXED = timezone(timedelta(hours=5, minutes=30))


def ensure_tzdata() -> None:
    """Import tzdata so ZoneInfo can resolve IANA keys on Windows."""
    try:
        import tzdata  # noqa: F401
    except ImportError:
        # Missing package: ZoneInfo may still work on Linux/macOS via system zones.
        pass


@lru_cache(maxsize=1)
def ist() -> tzinfo:
    """Asia/Kolkata (IST). Falls back to fixed +05:30 if zone data is unavailable."""
    ensure_tzdata()
    try:
        return ZoneInfo(_IST_KEY)
    except ZoneInfoNotFoundError:
        return _IST_FIXED


def now_ist() -> datetime:
    return datetime.now(tz=ist())


def format_ist_display(dt: datetime) -> str:
    """Return ``YYYY-MM-DD HH:MM IST`` for UI columns (Signals Updated, fills, etc.)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(ist())
    return local.strftime("%Y-%m-%d %H:%M IST")


def ist_day_start(d: date | None = None) -> datetime:
    """Midnight Asia/Kolkata for the given calendar day (default: today IST)."""
    day = d if d is not None else now_ist().date()
    return datetime.combine(day, time(0, 0), tzinfo=ist())


def calendar_quarter_bounds(d: date | None = None) -> tuple[date, date]:
    """Inclusive (start, end) dates of the IST calendar quarter containing ``d``."""
    day = d if d is not None else now_ist().date()
    q = (day.month - 1) // 3
    start_month = q * 3 + 1
    start = date(day.year, start_month, 1)
    if start_month + 3 > 12:
        end = date(day.year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(day.year, start_month + 3, 1) - timedelta(days=1)
    return start, end


def current_quarter_start(d: date | None = None) -> date:
    start, _ = calendar_quarter_bounds(d)
    return start
