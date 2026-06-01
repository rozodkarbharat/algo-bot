"""
Historical OHLCV candle storage — ONE document per stock per day.

Design rationale:
  Storing all candles for a single trading day in one document (the
  "bucket" pattern) dramatically reduces document count compared to one
  document per candle. For 15-min data over 50 NIFTY50 stocks across
  5 years that would be ~2.3M documents with individual storage vs
  ~62,500 day-buckets here.

  Benefits:
    - Efficient full-day reads (single document fetch instead of 25 reads)
    - Smaller index overhead
    - Natural alignment with how strategies consume data (day-by-day)
    - Easy "does today exist?" check before re-fetching

  Tradeoffs:
    - Individual candle updates require loading the full document
    - Slightly more complex query when seeking a single candle by time
      (handled by services that unpack the candles array)
"""

from datetime import datetime, timezone
from typing import Optional

from beanie import Document
from pydantic import BaseModel, Field
from pymongo import ASCENDING, IndexModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CandleData(BaseModel):
    """
    A single OHLCV candle.

    Stored as an embedded sub-document inside HistoricalCandle.candles[].
    The `time` field is UTC-aware and stores the candle open timestamp.
    """

    time: datetime = Field(..., description="Candle open timestamp (UTC)")
    open: float = Field(..., description="Open price")
    high: float = Field(..., description="High price")
    low: float = Field(..., description="Low price")
    close: float = Field(..., description="Close price")
    volume: int = Field(..., description="Total traded volume")

    class Config:
        # Allow creation from Angel One's raw list: [ts, o, h, l, c, v]
        populate_by_name = True


class HistoricalCandle(Document):
    """
    One-day bucket of OHLCV candles for a single instrument and interval.

    Collection: historical_candles
    Unique constraint: (symbol, trading_date, interval)
    """

    symbol: str = Field(..., description="NSE/BSE ticker symbol")
    exchange: str = Field(default="NSE", description="Exchange code")

    # CandleInterval string value — matches CandleInterval enum values.
    interval: str = Field(..., description="Candle interval (CandleInterval value)")

    # Stored as UTC midnight datetime, e.g. 2024-01-15T00:00:00+00:00
    # Represents the NSE trading session date for this bucket.
    trading_date: datetime = Field(
        ..., description="Trading date (UTC midnight) for this candle bucket"
    )

    # Ordered chronologically by candle open time (earliest first).
    candles: list[CandleData] = Field(default_factory=list)

    # Metadata — useful for incremental refresh and debugging.
    fetched_at: datetime = Field(
        default_factory=_utcnow,
        description="Timestamp when this bucket was last fetched/updated",
    )
    candle_count: int = Field(
        default=0, description="Cached count of candles (updated on save)"
    )

    class Settings:
        name = "historical_candles"
        indexes = [
            # Individual field indexes for single-dimension queries.
            IndexModel([("symbol", ASCENDING)]),
            IndexModel([("trading_date", ASCENDING)]),
            IndexModel([("interval", ASCENDING)]),
            # Compound unique index — enforces one bucket per (symbol, date, interval).
            # Also serves as the primary lookup index for the ingestion service.
            IndexModel(
                [("symbol", ASCENDING), ("trading_date", ASCENDING), ("interval", ASCENDING)],
                unique=True,
                name="symbol_date_interval_unique",
            ),
            # Reverse date index — used by get_latest_candle_date() queries.
            IndexModel(
                [("symbol", ASCENDING), ("interval", ASCENDING), ("trading_date", ASCENDING)],
                name="symbol_interval_date",
            ),
        ]

    def sync_candle_count(self) -> None:
        """Keep candle_count in sync before saving."""
        self.candle_count = len(self.candles)
        self.fetched_at = _utcnow()
