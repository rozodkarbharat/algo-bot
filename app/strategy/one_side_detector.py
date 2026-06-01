"""
One-Side Day detection engine.

Pure strategy logic — NO database calls, NO broker imports.
Receives candle data, emits a strongly-typed detection result.

Strategy definition:

  A "one-side day" is defined using the first 15-minute candle (ORB):
    orb_high = first_candle.high
    orb_low  = first_candle.low

  VALID BULLISH ONE-SIDE DAY:
    1. At some point during the rest of the day price crossed orb_high
    2. Price NEVER crossed orb_low at any point during the day
    3. Maximum move above orb_high >= min_move_percent (default 1%)

  VALID BEARISH ONE-SIDE DAY:
    1. At some point during the rest of the day price crossed orb_low
    2. Price NEVER crossed orb_high at any point during the day
    3. Maximum move below orb_low >= min_move_percent (default 1%)

  CHOPPY / INVALID:
    - Both orb_high AND orb_low were crossed at any point during the day
    - OR breakout did happen but move was < min_move_percent

  Direction does NOT matter for continuation probability — only "was it one-sided?"

Scalability notes:
  - Pure Python — safe to run in thread-pool executor for backtesting.
  - No state between calls — each detect() call is fully self-contained.
  - Input is a simple list[CandleData] so it works with any data source.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.models.historical_candle import CandleData
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Expected open time of the first 15-min candle in UTC hours (9:15 IST = 03:45 UTC)
_FIRST_CANDLE_UTC_HOUR = 3
_FIRST_CANDLE_UTC_MINUTE = 45


@dataclass(frozen=True)
class OneSideDetectionResult:
    """
    Strongly typed result returned by OneSideDayDetector.detect().

    All fields are populated for every call — None values indicate the concept
    does not apply (e.g. breakout_price is None for choppy days).
    """

    # ── Core classification ───────────────────────────────────────────────────
    is_one_side: bool
    direction: Optional[str]      # "UP", "DOWN", or None
    continuation_candidate: bool  # alias for is_one_side (clean interface)

    # ── Opening range ─────────────────────────────────────────────────────────
    first_candle_high: float
    first_candle_low: float

    # ── Breakout details (populated only when is_one_side=True) ──────────────
    breakout_price: Optional[float]   # orb_high (UP) or orb_low (DOWN)
    breakout_time: Optional[datetime]  # UTC timestamp of breakout candle
    move_percent: Optional[float]     # % move from breakout level to day extreme

    # ── Validity flags ────────────────────────────────────────────────────────
    opposite_side_crossed: bool  # True when both H and L were crossed (choppy)

    # ── Diagnostics ──────────────────────────────────────────────────────────
    rejection_reason: Optional[str]  # human-readable reason when is_one_side=False
    candle_count: int                # number of candles processed


class OneSideDayDetector:
    """
    Detects one-side days from a list of 15-minute candles for a single trading day.

    Usage:
        detector = OneSideDayDetector(min_move_percent=1.0)
        result = detector.detect(candles)
        if result.is_one_side:
            print(result.direction, result.move_percent)
    """

    def __init__(self, min_move_percent: float = 1.0) -> None:
        """
        Args:
            min_move_percent: Minimum % move required from the breakout level
                              to the day extreme for the day to be classified
                              as a valid one-side day. Default: 1.0%.
        """
        self.min_move_percent = min_move_percent

    # ── Public interface ──────────────────────────────────────────────────────

    def detect(self, candles: list[CandleData]) -> OneSideDetectionResult:
        """
        Classify a trading day as bullish OSD, bearish OSD, or invalid.

        Args:
            candles: All 15-minute candles for one trading day, sorted chronologically.
                     The first element must be the 9:15 AM candle (ORB candle).

        Returns:
            OneSideDetectionResult with all classification fields populated.
        """
        # ── Edge-case guards ──────────────────────────────────────────────────
        if not candles:
            return self._invalid("No candles provided.")

        if len(candles) < 2:
            return self._invalid(
                f"Only {len(candles)} candle(s); need at least 2 (ORB + 1 subsequent).",
                first_candle=candles[0] if candles else None,
            )

        first_candle = candles[0]
        remaining = candles[1:]

        # Validate first candle basic integrity
        if first_candle.high < first_candle.low:
            return self._invalid(
                "First candle high < low — data integrity error.",
                first_candle=first_candle,
            )

        orb_high = first_candle.high
        orb_low = first_candle.low

        # ── Scan remaining candles ────────────────────────────────────────────
        high_crossed_candle: Optional[CandleData] = None
        low_crossed_candle: Optional[CandleData] = None

        for candle in remaining:
            if high_crossed_candle is None and candle.high > orb_high:
                high_crossed_candle = candle
            if low_crossed_candle is None and candle.low < orb_low:
                low_crossed_candle = candle

        high_crossed = high_crossed_candle is not None
        low_crossed = low_crossed_candle is not None

        # ── Classification ────────────────────────────────────────────────────
        if high_crossed and low_crossed:
            # Both sides crossed → choppy day
            return OneSideDetectionResult(
                is_one_side=False,
                direction=None,
                continuation_candidate=False,
                first_candle_high=orb_high,
                first_candle_low=orb_low,
                breakout_price=None,
                breakout_time=None,
                move_percent=None,
                opposite_side_crossed=True,
                rejection_reason="Both ORB high and low were crossed (choppy day).",
                candle_count=len(candles),
            )

        if not high_crossed and not low_crossed:
            # Price never left the ORB — extremely range-bound day
            return OneSideDetectionResult(
                is_one_side=False,
                direction=None,
                continuation_candidate=False,
                first_candle_high=orb_high,
                first_candle_low=orb_low,
                breakout_price=None,
                breakout_time=None,
                move_percent=None,
                opposite_side_crossed=False,
                rejection_reason="Price never crossed either ORB boundary.",
                candle_count=len(candles),
            )

        if high_crossed and not low_crossed:
            return self._classify_bullish(
                orb_high=orb_high,
                orb_low=orb_low,
                breakout_candle=high_crossed_candle,  # type: ignore[arg-type]
                remaining=remaining,
                candle_count=len(candles),
            )

        # low_crossed and not high_crossed
        return self._classify_bearish(
            orb_high=orb_high,
            orb_low=orb_low,
            breakout_candle=low_crossed_candle,  # type: ignore[arg-type]
            remaining=remaining,
            candle_count=len(candles),
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    def _classify_bullish(
        self,
        orb_high: float,
        orb_low: float,
        breakout_candle: CandleData,
        remaining: list[CandleData],
        candle_count: int,
    ) -> OneSideDetectionResult:
        """Evaluate a potential bullish one-side day."""
        day_high = max(c.high for c in remaining)
        move_pct = _move_percent(day_high, orb_high)

        if move_pct < self.min_move_percent:
            return OneSideDetectionResult(
                is_one_side=False,
                direction="UP",
                continuation_candidate=False,
                first_candle_high=orb_high,
                first_candle_low=orb_low,
                breakout_price=orb_high,
                breakout_time=breakout_candle.time,
                move_percent=round(move_pct, 4),
                opposite_side_crossed=False,
                rejection_reason=(
                    f"Bullish breakout but move {move_pct:.2f}% < "
                    f"minimum {self.min_move_percent:.2f}%."
                ),
                candle_count=candle_count,
            )

        return OneSideDetectionResult(
            is_one_side=True,
            direction="UP",
            continuation_candidate=True,
            first_candle_high=orb_high,
            first_candle_low=orb_low,
            breakout_price=orb_high,
            breakout_time=breakout_candle.time,
            move_percent=round(move_pct, 4),
            opposite_side_crossed=False,
            rejection_reason=None,
            candle_count=candle_count,
        )

    def _classify_bearish(
        self,
        orb_high: float,
        orb_low: float,
        breakout_candle: CandleData,
        remaining: list[CandleData],
        candle_count: int,
    ) -> OneSideDetectionResult:
        """Evaluate a potential bearish one-side day."""
        day_low = min(c.low for c in remaining)
        move_pct = _move_percent(orb_low, day_low)

        if move_pct < self.min_move_percent:
            return OneSideDetectionResult(
                is_one_side=False,
                direction="DOWN",
                continuation_candidate=False,
                first_candle_high=orb_high,
                first_candle_low=orb_low,
                breakout_price=orb_low,
                breakout_time=breakout_candle.time,
                move_percent=round(move_pct, 4),
                opposite_side_crossed=False,
                rejection_reason=(
                    f"Bearish breakdown but move {move_pct:.2f}% < "
                    f"minimum {self.min_move_percent:.2f}%."
                ),
                candle_count=candle_count,
            )

        return OneSideDetectionResult(
            is_one_side=True,
            direction="DOWN",
            continuation_candidate=True,
            first_candle_high=orb_high,
            first_candle_low=orb_low,
            breakout_price=orb_low,
            breakout_time=breakout_candle.time,
            move_percent=round(move_pct, 4),
            opposite_side_crossed=False,
            rejection_reason=None,
            candle_count=candle_count,
        )

    def _invalid(
        self,
        reason: str,
        first_candle: Optional[CandleData] = None,
    ) -> OneSideDetectionResult:
        """Return a consistently structured result for unprocessable inputs."""
        high = first_candle.high if first_candle else 0.0
        low = first_candle.low if first_candle else 0.0
        return OneSideDetectionResult(
            is_one_side=False,
            direction=None,
            continuation_candidate=False,
            first_candle_high=high,
            first_candle_low=low,
            breakout_price=None,
            breakout_time=None,
            move_percent=None,
            opposite_side_crossed=False,
            rejection_reason=reason,
            candle_count=0,
        )


# ── Module-level default instance (stateless, reuse freely) ──────────────────

default_detector = OneSideDayDetector()


# ── Pure helper functions (importable by analytics.py) ───────────────────────

def _move_percent(higher_price: float, lower_price: float) -> float:
    """
    Calculate percentage move: (higher - lower) / lower * 100.

    Used for both bullish (day_high vs orb_high) and bearish (orb_low vs day_low).
    Always returns a non-negative value.
    """
    if lower_price <= 0:
        return 0.0
    return abs((higher_price - lower_price) / lower_price) * 100


def group_candles_by_day(
    candles: list[CandleData],
) -> dict[str, list[CandleData]]:
    """
    Group a flat candle list by trading date (YYYY-MM-DD string key in IST).

    Useful for batch processing multi-day candle streams from the data service.
    The result dicts are sorted chronologically within each day.
    """
    import pytz
    IST = pytz.timezone("Asia/Kolkata")

    grouped: dict[str, list[CandleData]] = {}
    for candle in candles:
        day_key = candle.time.astimezone(IST).date().isoformat()
        grouped.setdefault(day_key, []).append(candle)

    # Sort each day's candles by time
    for key in grouped:
        grouped[key].sort(key=lambda c: c.time)

    return grouped
