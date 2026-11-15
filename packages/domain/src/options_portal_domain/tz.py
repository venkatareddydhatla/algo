"""IANA timezone helpers — ensure tzdata on Windows (no system zoneinfo)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
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
