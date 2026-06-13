"""Tests for last_completed_trading_day (session-complete logic)."""

from datetime import date
from unittest.mock import patch

from app.utils.trading_day import (
    last_completed_trading_day,
    upcoming_trading_session,
)


@patch("app.utils.market_time.now_ist")
@patch("app.utils.trading_day.today_ist")
def test_before_market_close_returns_previous_day(
    mock_today, mock_now
) -> None:
    """At 09:00 IST on a Tuesday, yesterday (Monday) is the last complete session."""
    d = date(2026, 6, 2)  # Tuesday
    mock_today.return_value = d
    from app.utils.market_time import ist_datetime

    mock_now.return_value = ist_datetime(2026, 6, 2, 9, 0)
    assert last_completed_trading_day() == date(2026, 6, 1)


@patch("app.utils.market_time.now_ist")
@patch("app.utils.trading_day.today_ist")
def test_after_market_close_returns_today(mock_today, mock_now) -> None:
    d = date(2026, 6, 2)
    mock_today.return_value = d
    from app.utils.market_time import ist_datetime

    mock_now.return_value = ist_datetime(2026, 6, 2, 16, 0)
    assert last_completed_trading_day() == date(2026, 6, 2)


@patch("app.utils.trading_day.today_ist")
def test_weekend_returns_friday(mock_today) -> None:
    mock_today.return_value = date(2026, 6, 6)  # Saturday
    assert last_completed_trading_day() == date(2026, 6, 5)


# ── upcoming_trading_session: the session we're in or about to trade ──────────


@patch("app.utils.market_time.now_ist")
@patch("app.utils.trading_day.today_ist")
def test_upcoming_session_premarket_returns_today(mock_today, mock_now) -> None:
    """At 09:00 IST on a Friday, the session to trade is Friday itself."""
    d = date(2026, 6, 5)  # Friday
    mock_today.return_value = d
    from app.utils.market_time import ist_datetime

    mock_now.return_value = ist_datetime(2026, 6, 5, 9, 0)
    assert upcoming_trading_session() == date(2026, 6, 5)


@patch("app.utils.market_time.now_ist")
@patch("app.utils.trading_day.today_ist")
def test_upcoming_session_intraday_returns_today(mock_today, mock_now) -> None:
    """At 11:00 IST (mid-session) on a Friday, still Friday."""
    d = date(2026, 6, 5)  # Friday
    mock_today.return_value = d
    from app.utils.market_time import ist_datetime

    mock_now.return_value = ist_datetime(2026, 6, 5, 11, 0)
    assert upcoming_trading_session() == date(2026, 6, 5)


@patch("app.utils.market_time.now_ist")
@patch("app.utils.trading_day.today_ist")
def test_upcoming_session_after_close_returns_next_day(mock_today, mock_now) -> None:
    """After Friday's close the next session to trade is Monday."""
    d = date(2026, 6, 5)  # Friday
    mock_today.return_value = d
    from app.utils.market_time import ist_datetime

    mock_now.return_value = ist_datetime(2026, 6, 5, 16, 0)
    assert upcoming_trading_session() == date(2026, 6, 8)


@patch("app.utils.trading_day.today_ist")
def test_upcoming_session_weekend_returns_monday(mock_today) -> None:
    mock_today.return_value = date(2026, 6, 6)  # Saturday
    assert upcoming_trading_session() == date(2026, 6, 8)
