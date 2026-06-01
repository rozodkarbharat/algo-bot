"""
Intraday market state document — per-symbol, per-day live engine memory.

One document per (symbol, trading_date). Used by the live signal engine to
track first-candle completion, ORB boundaries, breakout detection, and the
trade-locked flag without rebuilding state from the candle stream on each tick.

The trade-locked flag enforces the one-trade-per-stock-per-day rule at the
service layer (the LiveSignal unique index is the database-level guarantee).

Persistence design:
  - Stored once per session per symbol.
  - Updated in place (upsert) as the first candle completes, the breakout is
    detected, and the signal is generated.
  - Cleared / reset by the market session engine before the next session.
"""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Optional

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, IndexModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class IntradayBreakoutSide(StrEnum):
    """Which side of the opening range was broken (mirrors LiveBreakoutSide)."""

    UP = "UP"
    DOWN = "DOWN"


class IntradayMarketState(Document):
    """
    Per-(symbol, trading_date) live engine state.

    Collection: intraday_market_state
    Unique constraint: (symbol, trading_date)
    """

    symbol: str = Field(..., description="NSE ticker symbol")
    trading_date: datetime = Field(..., description="Trading date (UTC midnight)")

    # First-candle tracking (the 09:15–09:30 IST ORB candle).
    first_candle_completed: bool = Field(default=False)
    first_candle_high: Optional[float] = Field(default=None)
    first_candle_low: Optional[float] = Field(default=None)
    first_candle_range_percent: Optional[float] = Field(
        default=None,
        description="(first_candle_high - first_candle_low) / first_candle_low * 100",
    )

    # Breakout tracking
    breakout_detected: bool = Field(default=False)
    breakout_side: Optional[IntradayBreakoutSide] = Field(default=None)

    # Signal & locking
    signal_generated: bool = Field(default=False)
    trade_locked: bool = Field(
        default=False,
        description="Once true, no more signals are emitted for this symbol today",
    )

    # Optional reference to the LiveSignal that locked this row.
    signal_id: Optional[str] = Field(default=None)

    # Free-form diagnostics (probability, ORB rejection reason, etc.).
    metadata: dict = Field(default_factory=dict)

    updated_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "intraday_market_state"
        indexes = [
            IndexModel([("symbol", ASCENDING)]),
            IndexModel([("trading_date", ASCENDING)]),
            IndexModel(
                [("symbol", ASCENDING), ("trading_date", ASCENDING)],
                unique=True,
                name="symbol_date_unique",
            ),
        ]

    def mark_updated(self) -> None:
        self.updated_at = _utcnow()
