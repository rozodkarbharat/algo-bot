"""
Market session engine — owns the trading-day clock for the live engine.

Responsibilities:
  - Decide whether the market is open / the engine should be running.
  - Detect the entry window (09:30 – 11:30 IST) for new signal entries.
  - Clear intraday state at session boundaries (next-day cutover).
  - Future-ready surface for NSE holiday calendar integration.

The session engine is intentionally side-effect light: it answers questions
about the current moment in IST and exposes a `reset_intraday_state()` hook
the live signal service calls daily (3:30 PM IST).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional

from app.config.settings import settings
from app.repositories.intraday_market_state_repository import (
    IntradayMarketStateRepository,
)
from app.utils.logger import get_logger
from app.utils.market_time import (
    IST,
    MARKET_CLOSE_TIME,
    MARKET_OPEN_TIME,
    date_to_utc_midnight,
    now_ist,
)
from app.utils.trading_day import is_trading_day, today_ist

logger = get_logger(__name__)


# ── First candle (ORB) and entry window constants ────────────────────────────

FIRST_CANDLE_OPEN: time = MARKET_OPEN_TIME           # 09:15 IST
FIRST_CANDLE_CLOSE: time = time(9, 30)               # 09:30 IST (exclusive)


def _parse_hhmm(value: str) -> time:
    """Parse 'HH:MM' into a datetime.time. Falls back to 11:30 on errors."""
    try:
        hh, mm = value.split(":", 1)
        return time(int(hh), int(mm))
    except Exception:  # noqa: BLE001 — fail-soft to a safe default
        logger.warning("Invalid HH:MM value %r; defaulting to 11:30", value)
        return time(11, 30)


# Entry window upper bound is configurable independently from the backtester,
# so live deployment can tighten/widen without touching the historical engine.
LATEST_ENTRY_TIME: time = _parse_hhmm(settings.LIVE_MAX_ENTRY_TIME_IST)


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SessionSnapshot:
    """Point-in-time view of the trading session clock."""

    now_ist: datetime
    trading_date: date
    is_trading_day: bool
    is_market_open: bool
    first_candle_completed: bool   # 09:30 IST or later
    entry_window_open: bool        # 09:30 ≤ now < LATEST_ENTRY_TIME
    after_entry_window: bool       # now ≥ LATEST_ENTRY_TIME


# ── Engine ───────────────────────────────────────────────────────────────────

class MarketSessionEngine:
    """
    Provides the answers the live engine needs about "where are we in the day?"

    Stateless except for the repository handle used to clear intraday rows.
    Safe to instantiate freely.
    """

    def __init__(
        self,
        state_repo: Optional[IntradayMarketStateRepository] = None,
    ) -> None:
        self._state_repo = state_repo or IntradayMarketStateRepository()

    # ── Clock helpers ─────────────────────────────────────────────────────────

    def snapshot(self, at: Optional[datetime] = None) -> SessionSnapshot:
        """Return a SessionSnapshot for `at` (defaults to now IST)."""
        moment = at.astimezone(IST) if at is not None else now_ist()
        trading_date = moment.date()
        td = is_trading_day(trading_date)
        t = moment.time()

        market_open = td and (MARKET_OPEN_TIME <= t < MARKET_CLOSE_TIME)
        first_completed = td and t >= FIRST_CANDLE_CLOSE
        entry_window_open = td and (FIRST_CANDLE_CLOSE <= t < LATEST_ENTRY_TIME)
        after_entry = td and t >= LATEST_ENTRY_TIME

        return SessionSnapshot(
            now_ist=moment,
            trading_date=trading_date,
            is_trading_day=td,
            is_market_open=market_open,
            first_candle_completed=first_completed,
            entry_window_open=entry_window_open,
            after_entry_window=after_entry,
        )

    def current_trading_date(self) -> date:
        """Today's date in IST. Holiday calendar integration is a future TODO."""
        return today_ist()

    def is_within_entry_window(self, at: Optional[datetime] = None) -> bool:
        """True iff `at` falls inside the 09:30–11:30 entry window."""
        return self.snapshot(at).entry_window_open

    def first_candle_completed_for(self, candle_end: datetime) -> bool:
        """Convenience: did `candle_end` (UTC) close at-or-after 09:30 IST?"""
        return candle_end.astimezone(IST).time() >= FIRST_CANDLE_CLOSE

    def time_until_first_candle_close(
        self, at: Optional[datetime] = None
    ) -> timedelta:
        """
        Wall-clock seconds remaining until 09:30 IST today.
        Returns timedelta(0) once the moment has passed.
        """
        moment = at.astimezone(IST) if at is not None else now_ist()
        target = IST.localize(
            datetime.combine(moment.date(), FIRST_CANDLE_CLOSE)
        )
        if moment >= target:
            return timedelta(0)
        return target - moment

    # ── Session lifecycle ─────────────────────────────────────────────────────

    async def reset_intraday_state(
        self, trading_date: Optional[date] = None
    ) -> int:
        """
        Clear IntradayMarketState rows for `trading_date` (today by default).

        Called by the scheduler's session cleanup job at 15:30 IST so the next
        session boots with a clean slate. Returns count of deleted rows.
        """
        d = trading_date or self.current_trading_date()
        deleted = await self._state_repo.delete_for_date(date_to_utc_midnight(d))
        logger.info("Intraday state reset: %d rows cleared for %s.", deleted, d)
        return deleted
