"""
Unit tests for MarketSessionEngine.

Validates the snapshot helpers used by the live engine to gate signal
generation. State-reset behaviour is intentionally not tested here — that
requires a database fixture and lives in integration tests.
"""

from __future__ import annotations

from datetime import datetime

import pytest
import pytz

from app.live.market_session import (
    FIRST_CANDLE_CLOSE,
    LATEST_ENTRY_TIME,
    MarketSessionEngine,
)

IST = pytz.timezone("Asia/Kolkata")


def _ist(hour: int, minute: int) -> datetime:
    # 2024-01-15 is a Monday — a trading day.
    return IST.localize(datetime(2024, 1, 15, hour, minute))


@pytest.fixture
def session() -> MarketSessionEngine:
    return MarketSessionEngine()


class TestSnapshot:
    def test_pre_open_state(self, session: MarketSessionEngine) -> None:
        snap = session.snapshot(at=_ist(9, 0))
        assert snap.is_trading_day is True
        assert snap.is_market_open is False
        assert snap.first_candle_completed is False
        assert snap.entry_window_open is False

    def test_during_first_candle(self, session: MarketSessionEngine) -> None:
        snap = session.snapshot(at=_ist(9, 20))
        assert snap.is_market_open is True
        assert snap.first_candle_completed is False
        assert snap.entry_window_open is False

    def test_entry_window_open_at_0930(self, session: MarketSessionEngine) -> None:
        snap = session.snapshot(at=_ist(FIRST_CANDLE_CLOSE.hour, FIRST_CANDLE_CLOSE.minute))
        assert snap.first_candle_completed is True
        assert snap.entry_window_open is True

    def test_entry_window_closes_at_latest(self, session: MarketSessionEngine) -> None:
        snap = session.snapshot(at=_ist(LATEST_ENTRY_TIME.hour, LATEST_ENTRY_TIME.minute))
        assert snap.entry_window_open is False
        assert snap.after_entry_window is True

    def test_weekend_is_not_trading_day(self, session: MarketSessionEngine) -> None:
        sat = IST.localize(datetime(2024, 1, 13, 10, 0))  # Saturday
        snap = session.snapshot(at=sat)
        assert snap.is_trading_day is False
        assert snap.is_market_open is False
        assert snap.entry_window_open is False
