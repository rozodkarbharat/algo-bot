"""
Trading day calendar utilities.

Provides weekday-aware date arithmetic for NSE/BSE markets.

NOTE: This implementation uses a simple weekday filter (Mon–Fri).
      Exchange holidays are NOT excluded. A production system should
      integrate with NSE's official holiday list (stored in MongoDB)
      for accurate trading-day checks.
"""

from datetime import date, timedelta

from app.utils.market_time import now_ist


def is_trading_day(d: date) -> bool:
    """
    Return True if d is a weekday (Mon–Fri).

    Weekdays: Monday=0 … Friday=4, Saturday=5, Sunday=6.
    """
    return d.weekday() < 5


def get_previous_trading_day(d: date | None = None) -> date:
    """
    Return the most recent trading day strictly before d.

    Defaults to today (IST) if d is None.
    """
    if d is None:
        d = now_ist().date()
    candidate = d - timedelta(days=1)
    while not is_trading_day(candidate):
        candidate -= timedelta(days=1)
    return candidate


def get_next_trading_day(d: date | None = None) -> date:
    """Return the next trading day strictly after d."""
    if d is None:
        d = now_ist().date()
    candidate = d + timedelta(days=1)
    while not is_trading_day(candidate):
        candidate += timedelta(days=1)
    return candidate


def get_trading_days(from_date: date, to_date: date) -> list[date]:
    """
    Return all trading days (weekdays) in [from_date, to_date] inclusive.

    The list is ordered chronologically.
    """
    days: list[date] = []
    current = from_date
    while current <= to_date:
        if is_trading_day(current):
            days.append(current)
        current += timedelta(days=1)
    return days


def split_date_range(
    from_date: date, to_date: date, chunk_days: int
) -> list[tuple[date, date]]:
    """
    Split [from_date, to_date] into chunks of at most chunk_days calendar days.

    Used to paginate Angel One API requests that cap the date range per call.

    Returns a list of (chunk_start, chunk_end) tuples.
    """
    chunks: list[tuple[date, date]] = []
    current = from_date
    while current <= to_date:
        chunk_end = min(current + timedelta(days=chunk_days - 1), to_date)
        chunks.append((current, chunk_end))
        current = chunk_end + timedelta(days=1)
    return chunks


def today_ist() -> date:
    """Return today's date in IST."""
    return now_ist().date()


def last_completed_trading_day() -> date:
    """
    Return the last trading day for which complete data is available.

    If market is currently open (or today is a non-trading day), return
    the previous trading day — today's data is incomplete until 15:30.
    """
    from app.utils.market_time import is_market_open

    today = today_ist()
    if is_market_open() or not is_trading_day(today):
        return get_previous_trading_day(today)
    return today
