"""
Candle API response schemas.

The API returns candles in a flat list (not the raw bucket structure stored
in MongoDB) so the frontend doesn't need to know about the day-bucket design.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class CandleDataResponse(BaseModel):
    """A single OHLCV candle."""

    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int

    model_config = {"from_attributes": True}


class CandleBucketResponse(BaseModel):
    """
    One day-bucket exactly as stored in MongoDB.
    Useful for admin/debug endpoints.
    """

    symbol: str
    exchange: str
    interval: str
    trading_date: datetime
    candle_count: int
    fetched_at: datetime
    candles: list[CandleDataResponse]

    model_config = {"from_attributes": True}


class CandleQueryParams(BaseModel):
    """Query parameters for the candle list endpoint."""

    from_date: Optional[str] = Field(None, description="Start date YYYY-MM-DD")
    to_date: Optional[str] = Field(None, description="End date YYYY-MM-DD")
    interval: str = Field(default="FIFTEEN_MINUTE", description="CandleInterval value")
    limit: int = Field(default=100, ge=1, le=2000, description="Max candles to return")
