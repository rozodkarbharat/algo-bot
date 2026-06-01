"""
Market timezone and trading-hours utilities.

All IST-aware helpers are centralised here so no other module needs to
import pytz or hard-code timezone strings. Functions follow the convention:
  - Input datetimes may be naive (assumed IST) or aware.
  - Output datetimes are always timezone-aware.
"""

from datetime import date, datetime, time, timedelta, timezone

import pytz

# ── Timezone constants ────────────────────────────────────────────────────────

IST: pytz.BaseTzInfo = pytz.timezone("Asia/Kolkata")
UTC: timezone = timezone.utc

# NSE/BSE regular session hours (IST)
MARKET_OPEN_TIME: time = time(9, 15)
MARKET_CLOSE_TIME: time = time(15, 30)

# Pre-market session (for reference / future use)
PRE_MARKET_OPEN: time = time(9, 0)
PRE_MARKET_CLOSE: time = time(9, 8)


# ── Conversion helpers ────────────────────────────────────────────────────────

def now_ist() -> datetime:
    """Return current time in IST (timezone-aware)."""
    return datetime.now(IST)


def now_utc() -> datetime:
    """Return current time in UTC (timezone-aware)."""
    return datetime.now(UTC)


def to_ist(dt: datetime) -> datetime:
    """Convert any timezone-aware datetime to IST."""
    return dt.astimezone(IST)


def to_utc(dt: datetime) -> datetime:
    """Convert any timezone-aware datetime to UTC."""
    return dt.astimezone(UTC)


def ist_datetime(
    year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0
) -> datetime:
    """Construct an IST-aware datetime from components."""
    return IST.localize(datetime(year, month, day, hour, minute, second))


def date_to_utc_midnight(d: date) -> datetime:
    """
    Convert a calendar date to a UTC midnight datetime.

    Used for storing trading_date in MongoDB — the canonical form for a
    trading day is midnight UTC so range queries work correctly.
    """
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def utc_midnight_to_date(dt: datetime) -> date:
    """Reverse of date_to_utc_midnight."""
    return dt.astimezone(UTC).date()


# ── Market session helpers ────────────────────────────────────────────────────

def is_market_open(dt: datetime | None = None) -> bool:
    """
    Return True if the given datetime falls within the regular NSE/BSE session.

    Does NOT account for exchange holidays — use is_trading_day() for that.
    """
    if dt is None:
        dt = now_ist()
    dt_ist = to_ist(dt)
    if dt_ist.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    t = dt_ist.time()
    return MARKET_OPEN_TIME <= t <= MARKET_CLOSE_TIME


def market_open_datetime(d: date) -> datetime:
    """Return the IST market open datetime for a given trading date."""
    return IST.localize(
        datetime(d.year, d.month, d.day, MARKET_OPEN_TIME.hour, MARKET_OPEN_TIME.minute)
    )


def market_close_datetime(d: date) -> datetime:
    """Return the IST market close datetime for a given trading date."""
    return IST.localize(
        datetime(d.year, d.month, d.day, MARKET_CLOSE_TIME.hour, MARKET_CLOSE_TIME.minute)
    )


def angel_one_date_str(dt: datetime) -> str:
    """
    Format a datetime as the string Angel One's historical API expects.

    Angel One format: "YYYY-MM-DD HH:MM" (space, no seconds, no timezone suffix).
    """
    return dt.strftime("%Y-%m-%d %H:%M")


def parse_angel_one_timestamp(ts: str) -> datetime:
    """
    Parse an ISO 8601 timestamp from Angel One's candle response.

    Angel One returns: "2024-01-15T09:15:00+05:30"
    Returns a UTC-aware datetime.
    """
    # Python 3.11+ handles %z with colon; use fromisoformat which handles both.
    try:
        dt = datetime.fromisoformat(ts)
        return dt.astimezone(UTC)
    except ValueError:
        # Fallback for edge-case formats
        dt = datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
        return IST.localize(dt).astimezone(UTC)
