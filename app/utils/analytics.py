"""
Analytics helper functions for the one-side day strategy.

Pure utility functions — no business logic, no DB access, no broker calls.
These are extracted here so they can be unit-tested in complete isolation
and reused across the strategy engine and services.
"""

from datetime import date, datetime
from typing import Optional

import pytz

from app.models.historical_candle import CandleData
from app.utils.market_time import IST

# ── Move percentage ───────────────────────────────────────────────────────────


def move_percent(reference_price: float, target_price: float) -> float:
    """
    Calculate percentage move from reference_price to target_price.

    Returns a signed float. Positive = upward move, negative = downward move.
    Caller is responsible for taking abs() when direction is already encoded.

    Raises:
        ValueError: If reference_price is zero (would produce division by zero).
    """
    if reference_price == 0.0:
        raise ValueError("reference_price cannot be zero.")
    return ((target_price - reference_price) / reference_price) * 100


def move_percent_abs(higher: float, lower: float) -> float:
    """
    Calculate the absolute percentage move between two price levels.

    Always returns a non-negative value regardless of argument order.
    """
    if lower <= 0:
        return 0.0
    return abs((higher - lower) / lower) * 100


# ── Breakout detection ────────────────────────────────────────────────────────


def find_breakout_candle(
    candles: list[CandleData], level: float, direction: str
) -> Optional[CandleData]:
    """
    Find the first candle that broke through a price level.

    Args:
        candles: Ordered list of candles to scan (excludes ORB candle itself).
        level: Price level to watch (orb_high for UP, orb_low for DOWN).
        direction: "UP" checks candle.high > level; "DOWN" checks candle.low < level.

    Returns:
        The first candle that crossed the level, or None if never crossed.
    """
    if direction == "UP":
        for candle in candles:
            if candle.high > level:
                return candle
    elif direction == "DOWN":
        for candle in candles:
            if candle.low < level:
                return candle
    return None


def opposite_side_violated(
    candles: list[CandleData], orb_high: float, orb_low: float
) -> bool:
    """
    Return True if BOTH orb_high and orb_low were crossed by any candle in the list.

    Used to detect choppy / two-sided days.
    """
    high_crossed = any(c.high > orb_high for c in candles)
    low_crossed = any(c.low < orb_low for c in candles)
    return high_crossed and low_crossed


# ── Trading day alignment ─────────────────────────────────────────────────────


def candle_trading_date_ist(candle: CandleData) -> date:
    """
    Return the NSE trading date (as a date in IST) for a given candle.

    Candle timestamps are stored in UTC. Converting to IST gives the correct
    trading session date (e.g. UTC 03:45 = IST 09:15 = trading day start).
    """
    return candle.time.astimezone(IST).date()


def group_candles_by_trading_date(
    candles: list[CandleData],
) -> dict[date, list[CandleData]]:
    """
    Partition a flat candle list into per-trading-day buckets.

    Keys are calendar dates in IST. Each bucket's candles are sorted
    chronologically (oldest first). Used by StrategyService for batch
    processing multi-day candle streams.
    """
    grouped: dict[date, list[CandleData]] = {}
    for candle in candles:
        day = candle_trading_date_ist(candle)
        grouped.setdefault(day, []).append(candle)

    for day in grouped:
        grouped[day].sort(key=lambda c: c.time)

    return grouped


def validate_candle_sequence(candles: list[CandleData]) -> Optional[str]:
    """
    Validate that a candle list is usable for OSD detection.

    Returns:
        None if valid, or a human-readable error string if invalid.
    """
    if not candles:
        return "Empty candle list."
    if len(candles) < 2:
        return f"Only {len(candles)} candle(s); minimum 2 required."

    for i, candle in enumerate(candles):
        if candle.high < candle.low:
            return f"Candle {i} has high < low (data integrity error)."
        if candle.open <= 0 or candle.close <= 0:
            return f"Candle {i} has non-positive open or close."
        if candle.volume < 0:
            return f"Candle {i} has negative volume."

    # Check chronological order
    for i in range(1, len(candles)):
        if candles[i].time <= candles[i - 1].time:
            return f"Candles are not in strict chronological order at index {i}."

    return None


# ── Candle selection helpers ──────────────────────────────────────────────────


def get_first_candle(candles: list[CandleData]) -> Optional[CandleData]:
    """Return the first (ORB) candle from an ordered list, or None if empty."""
    return candles[0] if candles else None


def get_remaining_candles(candles: list[CandleData]) -> list[CandleData]:
    """Return all candles after the first (ORB) candle."""
    return candles[1:] if len(candles) > 1 else []


def day_high(candles: list[CandleData]) -> float:
    """Return the highest high across all candles in the list."""
    return max(c.high for c in candles) if candles else 0.0


def day_low(candles: list[CandleData]) -> float:
    """Return the lowest low across all candles in the list."""
    return min(c.low for c in candles) if candles else float("inf")
