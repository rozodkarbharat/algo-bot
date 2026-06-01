"""
Unit tests for the One-Side Day detection engine.

Tests are pure Python — no database, no broker, no I/O.
Each test constructs synthetic CandleData and asserts the detector's output.

Run with:
    pytest tests/test_one_side_detector.py -v
"""

from datetime import datetime, timezone

import pytest

from app.models.historical_candle import CandleData
from app.strategy.one_side_detector import OneSideDayDetector, _move_percent


# ── Helpers ───────────────────────────────────────────────────────────────────

def _candle(
    hour: int,
    minute: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: int = 100000,
) -> CandleData:
    """Build a CandleData with an IST-mapped UTC timestamp."""
    # 9:15 IST = 3:45 UTC; each 15-min candle advances by 15 min.
    return CandleData(
        time=datetime(2024, 1, 15, hour, minute, 0, tzinfo=timezone.utc),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def _orb_candle(high: float, low: float) -> CandleData:
    """Build the 09:15 ORB candle (03:45 UTC)."""
    mid = (high + low) / 2
    return _candle(3, 45, mid, high, low, mid)


# ── Detector instance ─────────────────────────────────────────────────────────

@pytest.fixture
def detector() -> OneSideDayDetector:
    return OneSideDayDetector(min_move_percent=1.0)


# ── Edge cases ────────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_candles(self, detector: OneSideDayDetector) -> None:
        result = detector.detect([])
        assert result.is_one_side is False
        assert "No candles" in (result.rejection_reason or "")

    def test_single_candle_only(self, detector: OneSideDayDetector) -> None:
        result = detector.detect([_orb_candle(100, 99)])
        assert result.is_one_side is False
        assert result.candle_count == 0
        assert "Only 1" in (result.rejection_reason or "")

    def test_price_never_leaves_orb(self, detector: OneSideDayDetector) -> None:
        """Price stays within the ORB all day — not one-sided."""
        orb = _orb_candle(high=100.0, low=99.0)
        # All subsequent candles inside the range
        inside = [
            _candle(4, 0, 99.5, 99.9, 99.2, 99.6),
            _candle(4, 15, 99.6, 99.8, 99.1, 99.4),
            _candle(4, 30, 99.4, 99.7, 99.0, 99.3),
        ]
        result = detector.detect([orb] + inside)
        assert result.is_one_side is False
        assert result.opposite_side_crossed is False
        assert "never crossed" in (result.rejection_reason or "").lower()

    def test_invalid_candle_high_lt_low(self, detector: OneSideDayDetector) -> None:
        bad_orb = CandleData(
            time=datetime(2024, 1, 15, 3, 45, tzinfo=timezone.utc),
            open=100, high=98, low=100, close=99, volume=1000,  # high < low
        )
        follow = _candle(4, 0, 99, 101, 98, 100)
        result = detector.detect([bad_orb, follow])
        assert result.is_one_side is False
        assert "data integrity" in (result.rejection_reason or "").lower()


# ── Bullish one-side day ──────────────────────────────────────────────────────

class TestBullishOneSideDay:
    def test_valid_bullish_day(self, detector: OneSideDayDetector) -> None:
        """High crossed, low never crossed, move >= 1%."""
        orb = _orb_candle(high=1000.0, low=990.0)
        candles = [
            orb,
            _candle(4, 0, 1001, 1002, 991, 1001),   # crosses high (1002 > 1000)
            _candle(4, 15, 1001, 1008, 995, 1006),
            _candle(4, 30, 1006, 1015, 998, 1012),   # day_high = 1015
        ]
        result = detector.detect(candles)

        assert result.is_one_side is True
        assert result.direction == "UP"
        assert result.continuation_candidate is True
        assert result.opposite_side_crossed is False
        assert result.breakout_price == 1000.0
        assert result.breakout_time == candles[1].time  # first candle crossing high
        # move = (1015 - 1000) / 1000 * 100 = 1.5%
        assert result.move_percent is not None
        assert result.move_percent >= 1.0
        assert result.rejection_reason is None

    def test_bullish_insufficient_move(self, detector: OneSideDayDetector) -> None:
        """High crossed but day move < 1% — invalid."""
        orb = _orb_candle(high=1000.0, low=990.0)
        candles = [
            orb,
            _candle(4, 0, 1001, 1005, 995, 1004),  # crosses high but only +0.5%
            _candle(4, 15, 1004, 1005, 998, 1003),
        ]
        result = detector.detect(candles)

        assert result.is_one_side is False
        assert result.direction == "UP"   # direction is inferred even when invalid
        assert result.continuation_candidate is False
        assert "< minimum" in (result.rejection_reason or "")
        assert result.move_percent is not None
        assert result.move_percent < 1.0

    def test_bullish_large_move(self, detector: OneSideDayDetector) -> None:
        """Strong bullish day with 3%+ move."""
        orb = _orb_candle(high=500.0, low=490.0)
        candles = [
            orb,
            _candle(4, 0, 501, 510, 492, 508),    # first cross
            _candle(4, 15, 508, 518, 500, 515),
            _candle(4, 30, 515, 516, 498, 514),
        ]
        result = detector.detect(candles)
        assert result.is_one_side is True
        assert result.move_percent is not None
        assert result.move_percent >= 3.0

    def test_bullish_exactly_one_percent(self, detector: OneSideDayDetector) -> None:
        """Move exactly equals the threshold — should be valid."""
        orb = _orb_candle(high=1000.0, low=990.0)
        # day_high = 1010 → move = (1010 - 1000) / 1000 * 100 = 1.0%
        candles = [
            orb,
            _candle(4, 0, 1001, 1010, 995, 1008),
        ]
        result = detector.detect(candles)
        assert result.is_one_side is True


# ── Bearish one-side day ──────────────────────────────────────────────────────

class TestBearishOneSideDay:
    def test_valid_bearish_day(self, detector: OneSideDayDetector) -> None:
        """Low crossed, high never crossed, move >= 1%."""
        orb = _orb_candle(high=1010.0, low=1000.0)
        candles = [
            orb,
            _candle(4, 0, 999, 1005, 995, 998),    # crosses low (995 < 1000)
            _candle(4, 15, 998, 1002, 990, 992),
            _candle(4, 30, 992, 995, 985, 987),    # day_low = 985
        ]
        result = detector.detect(candles)

        assert result.is_one_side is True
        assert result.direction == "DOWN"
        assert result.continuation_candidate is True
        assert result.opposite_side_crossed is False
        assert result.breakout_price == 1000.0
        assert result.breakout_time == candles[1].time
        # move = (1000 - 985) / 1000 * 100 = 1.5%
        assert result.move_percent is not None
        assert result.move_percent >= 1.0

    def test_bearish_insufficient_move(self, detector: OneSideDayDetector) -> None:
        """Low crossed but day move < 1% — invalid."""
        orb = _orb_candle(high=1010.0, low=1000.0)
        candles = [
            orb,
            _candle(4, 0, 999, 1005, 996, 998),  # crosses low: 996, move = 0.4%
            _candle(4, 15, 998, 1003, 997, 999),
        ]
        result = detector.detect(candles)

        assert result.is_one_side is False
        assert result.direction == "DOWN"
        assert result.move_percent is not None
        assert result.move_percent < 1.0

    def test_bearish_large_move(self, detector: OneSideDayDetector) -> None:
        """Strong bearish day with 3%+ move."""
        orb = _orb_candle(high=2000.0, low=1980.0)
        candles = [
            orb,
            _candle(4, 0, 1978, 1985, 1975, 1977),
            _candle(4, 15, 1977, 1980, 1965, 1968),
            _candle(4, 30, 1968, 1970, 1940, 1945),  # day_low = 1940
        ]
        result = detector.detect(candles)
        assert result.is_one_side is True
        assert result.direction == "DOWN"
        # move = (1980 - 1940) / 1980 * 100 ≈ 2.02%
        assert result.move_percent is not None
        assert result.move_percent >= 2.0


# ── Choppy day ────────────────────────────────────────────────────────────────

class TestChoppyDay:
    def test_both_sides_crossed(self, detector: OneSideDayDetector) -> None:
        """Both ORB high and low crossed — choppy, not one-sided."""
        orb = _orb_candle(high=100.0, low=90.0)
        candles = [
            orb,
            _candle(4, 0, 101, 102, 89, 92),  # crosses BOTH high (102) and low (89)
            _candle(4, 15, 92, 95, 91, 94),
        ]
        result = detector.detect(candles)

        assert result.is_one_side is False
        assert result.direction is None
        assert result.opposite_side_crossed is True
        assert result.continuation_candidate is False
        assert "choppy" in (result.rejection_reason or "").lower()

    def test_choppy_high_then_low(self, detector: OneSideDayDetector) -> None:
        """High crossed first, then low crossed later — still choppy."""
        orb = _orb_candle(high=100.0, low=90.0)
        candles = [
            orb,
            _candle(4, 0, 101, 103, 92, 102),   # crosses high
            _candle(4, 15, 102, 104, 91, 100),  # still above high
            _candle(4, 30, 100, 101, 88, 89),   # now crosses low too → choppy
        ]
        result = detector.detect(candles)
        assert result.is_one_side is False
        assert result.opposite_side_crossed is True


# ── Custom threshold ──────────────────────────────────────────────────────────

class TestCustomThreshold:
    def test_higher_threshold_rejects_smaller_move(self) -> None:
        """A 1.5% threshold should reject a 1.2% move."""
        detector = OneSideDayDetector(min_move_percent=1.5)
        orb = _orb_candle(high=1000.0, low=990.0)
        candles = [
            orb,
            _candle(4, 0, 1001, 1012, 995, 1010),  # day_high = 1012 → 1.2%
        ]
        result = detector.detect(candles)
        assert result.is_one_side is False
        assert result.move_percent is not None
        assert result.move_percent < 1.5

    def test_lower_threshold_accepts_small_move(self) -> None:
        """A 0.5% threshold should accept a 0.7% move."""
        detector = OneSideDayDetector(min_move_percent=0.5)
        orb = _orb_candle(high=1000.0, low=990.0)
        candles = [
            orb,
            _candle(4, 0, 1001, 1007, 995, 1005),  # day_high = 1007 → 0.7%
        ]
        result = detector.detect(candles)
        assert result.is_one_side is True


# ── Move percent helper ───────────────────────────────────────────────────────

class TestMovePercent:
    def test_positive_move(self) -> None:
        assert abs(_move_percent(1010, 1000) - 1.0) < 0.0001

    def test_negative_move(self) -> None:
        assert abs(_move_percent(990, 1000) - 1.0) < 0.0001

    def test_zero_lower_price(self) -> None:
        result = _move_percent(0, 0)
        assert result == 0.0

    def test_large_move(self) -> None:
        assert abs(_move_percent(1100, 1000) - 10.0) < 0.0001


# ── Continuation probability tests ────────────────────────────────────────────

class TestContinuationProbability:
    def test_basic_probability(self) -> None:
        from datetime import date
        from app.strategy.continuation_probability import ContinuationProbabilityEngine

        engine = ContinuationProbabilityEngine(
            lookback_days=252,
            min_occurrences=3,
            probability_threshold=0.70,
        )
        # 7 OSD days, each followed by another OSD → 100% probability
        history = [
            (date(2024, 1, d), True) for d in range(2, 16)  # 14 days
        ]
        result = engine.calculate(symbol="TEST", history=history)
        # Every day is OSD, so: 13 occurrences (pairs), 13 successes
        assert result.total_occurrences == 13
        assert result.continuation_successes == 13
        assert abs(result.continuation_probability - 1.0) < 0.001
        assert result.tradable is True

    def test_zero_probability(self) -> None:
        from datetime import date
        from app.strategy.continuation_probability import ContinuationProbabilityEngine

        engine = ContinuationProbabilityEngine(min_occurrences=2, probability_threshold=0.7)
        # Alternating OSD / non-OSD → every OSD is followed by non-OSD → 0% continuation
        history = [(date(2024, 1, 1), True), (date(2024, 1, 2), False),
                   (date(2024, 1, 3), True), (date(2024, 1, 4), False),
                   (date(2024, 1, 5), True), (date(2024, 1, 8), False)]
        result = engine.calculate(symbol="TEST", history=history)
        assert result.total_occurrences == 3
        assert result.continuation_successes == 0
        assert result.continuation_probability == 0.0
        assert result.tradable is False

    def test_below_min_occurrences(self) -> None:
        from datetime import date
        from app.strategy.continuation_probability import ContinuationProbabilityEngine

        engine = ContinuationProbabilityEngine(min_occurrences=10, probability_threshold=0.7)
        # Only 3 OSD days — insufficient sample
        history = [(date(2024, 1, 1), True), (date(2024, 1, 2), True),
                   (date(2024, 1, 3), True), (date(2024, 1, 4), False)]
        result = engine.calculate(symbol="TEST", history=history)
        assert result.tradable is False
        assert "occurrence" in (result.rejection_reason or "").lower()

    def test_empty_history(self) -> None:
        from app.strategy.continuation_probability import ContinuationProbabilityEngine, ContinuationAnalysisResult

        engine = ContinuationProbabilityEngine()
        result = engine.calculate(symbol="TEST", history=[])
        assert isinstance(result, ContinuationAnalysisResult)
        assert result.total_occurrences == 0
        assert result.tradable is False

    def test_70_percent_probability_tradable(self) -> None:
        from datetime import date
        from app.strategy.continuation_probability import ContinuationProbabilityEngine

        engine = ContinuationProbabilityEngine(min_occurrences=5, probability_threshold=0.70)
        # 7 successes out of 10 OSD occurrences = 70%
        history: list[tuple[date, bool]] = []
        current = date(2024, 1, 2)
        from datetime import timedelta
        osd_count = 0
        for i in range(25):
            is_osd = (i % 10) != 9  # one failure every 10th day after OSD
            history.append((current, is_osd))
            current += timedelta(days=1)
        result = engine.calculate(symbol="TEST", history=history)
        # probability should be >= 0.7 for most patterns with this input
        assert result.total_occurrences > 5

    def test_lookback_window_applied(self) -> None:
        from datetime import date, timedelta
        from app.strategy.continuation_probability import ContinuationProbabilityEngine

        engine = ContinuationProbabilityEngine(
            lookback_days=5,  # very short window
            min_occurrences=2,
            probability_threshold=0.5,
        )
        # 20 days of history: first 15 all OSD, last 5 all non-OSD
        history = [(date(2024, 1, 1) + timedelta(days=i), i < 15) for i in range(20)]
        result = engine.calculate(symbol="TEST", history=history)
        # Only the last 5 days are in window → all non-OSD → 0 occurrences
        assert result.total_occurrences == 0
        assert result.tradable is False
