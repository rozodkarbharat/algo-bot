"""
One-Side Day analysis result document.

One document per (symbol, trading_date). Stores the result of running the
one-side day detection algorithm against the first 15-minute candle.

A "one-side day" is a trading day where price breaks out of the opening range
in one direction and NEVER crosses the opposite side of the first candle.
Direction does not matter for statistical continuation tracking.
"""

from datetime import datetime, timezone
from typing import Optional

from beanie import Document
from pydantic import BaseModel, Field
from pymongo import ASCENDING, IndexModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OneSideDay(Document):
    """
    Stores the one-side day classification result for a single (symbol, date).

    Collection: one_side_days
    Unique constraint: (symbol, trading_date)
    """

    symbol: str = Field(..., description="NSE ticker symbol")

    # UTC midnight for the trading session date.
    trading_date: datetime = Field(..., description="Trading date (UTC midnight)")

    # Core classification
    is_one_side: bool = Field(
        default=False,
        description="True if the day was a valid one-side day",
    )
    direction: Optional[str] = Field(
        default=None,
        description="Direction of breakout: UP, DOWN, or None for choppy/invalid",
    )

    # First 15-min candle boundaries (the Opening Range)
    first_candle_high: float = Field(..., description="High of the first 15-min candle (ORB high)")
    first_candle_low: float = Field(..., description="Low of the first 15-min candle (ORB low)")

    # Breakout details — populated only when is_one_side=True
    breakout_price: Optional[float] = Field(
        default=None,
        description="Price level at which the one-side breakout was confirmed (orb_high or orb_low)",
    )
    breakout_time: Optional[datetime] = Field(
        default=None,
        description="UTC timestamp of the first candle that crossed the breakout level",
    )
    move_percent: Optional[float] = Field(
        default=None,
        description="% move from breakout level to day extreme (positive for both directions)",
    )

    # Validity flags
    opposite_side_crossed: bool = Field(
        default=False,
        description="True if both ORB high AND low were crossed (choppy/invalid day)",
    )
    continuation_candidate: bool = Field(
        default=False,
        description="True if this is a valid one-side day (is_one_side=True); used for shortlist filtering",
    )

    # Extensible metadata for debugging / future fields
    metadata: dict = Field(default_factory=dict)

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "one_side_days"
        indexes = [
            IndexModel([("symbol", ASCENDING)]),
            IndexModel([("trading_date", ASCENDING)]),
            # Primary lookup index — also enforces one result per (symbol, date).
            IndexModel(
                [("symbol", ASCENDING), ("trading_date", ASCENDING)],
                unique=True,
                name="symbol_date_unique",
            ),
        ]

    def mark_updated(self) -> None:
        self.updated_at = _utcnow()
