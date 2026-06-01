"""
Candle interval definitions.

Single source of truth for interval strings used across:
  - Angel One API requests
  - MongoDB document fields
  - Scheduler job configurations
  - Strategy engine lookups
"""

from enum import StrEnum


class CandleInterval(StrEnum):
    """
    Supported OHLCV candle intervals.

    Values match Angel One SmartAPI interval strings exactly so they can
    be passed directly to the API without any mapping layer.
    """

    ONE_MINUTE = "ONE_MINUTE"
    THREE_MINUTE = "THREE_MINUTE"
    FIVE_MINUTE = "FIVE_MINUTE"
    TEN_MINUTE = "TEN_MINUTE"
    FIFTEEN_MINUTE = "FIFTEEN_MINUTE"
    THIRTY_MINUTE = "THIRTY_MINUTE"
    ONE_HOUR = "ONE_HOUR"
    ONE_DAY = "ONE_DAY"


# Maximum days fetchable per API call per interval (Angel One limits).
# Used by the ingestion service to split large date ranges into valid chunks.
INTERVAL_MAX_DAYS: dict[CandleInterval, int] = {
    CandleInterval.ONE_MINUTE: 30,
    CandleInterval.THREE_MINUTE: 30,
    CandleInterval.FIVE_MINUTE: 30,
    CandleInterval.TEN_MINUTE: 30,
    CandleInterval.FIFTEEN_MINUTE: 60,
    CandleInterval.THIRTY_MINUTE: 60,
    CandleInterval.ONE_HOUR: 365,
    CandleInterval.ONE_DAY: 2000,
}

# Approximate candles per trading day per interval (NSE: 9:15–15:30 = 375 min).
CANDLES_PER_DAY: dict[CandleInterval, int] = {
    CandleInterval.ONE_MINUTE: 375,
    CandleInterval.THREE_MINUTE: 125,
    CandleInterval.FIVE_MINUTE: 75,
    CandleInterval.TEN_MINUTE: 38,
    CandleInterval.FIFTEEN_MINUTE: 25,
    CandleInterval.THIRTY_MINUTE: 13,
    CandleInterval.ONE_HOUR: 7,
    CandleInterval.ONE_DAY: 1,
}
