"""
Tests for ORHVSetupDetector (Phase 1 — Setup Detection).

All tests use pure in-memory data — zero DB or I/O dependencies.
"""

import pytest
from datetime import datetime, timezone

from app.models.historical_candle import CandleData
from app.strategy.strategies.opening_range_historical_validation.detector import (
    ORHVSetupDetector,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _utc(hour: int, minute: int = 0) -> datetime:
    """Create UTC datetime on a fixed test date."""
    return datetime(2024, 1, 15, hour, minute, tzinfo=timezone.utc)


def _candle(
    open_: float,
    high: float,
    low: float,
    close: float,
    hour: int,
    minute: int = 0,
) -> CandleData:
    return CandleData(
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=10000,
        time=_utc(hour, minute),
    )


def _orb_candle(high: float = 110.0, low: float = 100.0) -> CandleData:
    """Simulate the 9:15–9:30 opening-range candle (03:45 UTC open)."""
    return _candle(open_=105.0, high=high, low=low, close=107.0, hour=3, minute=45)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def detector():
    return ORHVSetupDetector()


# ── Edge-case tests ───────────────────────────────────────────────────────────

def test_no_candles_returns_not_candidate(detector):
    result = detector.detect([])
    assert not result.is_candidate
    assert result.candle_count == 0
    assert result.rejection_reason is not None


def test_single_candle_only_orb_returns_not_candidate(detector):
    result = detector.detect([_orb_candle()])
    assert not result.is_candidate
    assert "need at least one subsequent candle" in (result.rejection_reason or "").lower()


def test_data_integrity_error_high_lt_low(detector):
    bad = _candle(open_=100, high=90, low=110, close=100, hour=3, minute=45)
    result = detector.detect([bad])
    assert not result.is_candidate
    assert result.rejection_reason is not None


# ── CH1 / CL1 not found ───────────────────────────────────────────────────────

def test_neither_orh_nor_orl_broken(detector):
    orb = _orb_candle(high=110, low=100)
    # All subsequent candles stay within ORB
    candles = [
        orb,
        _candle(101, 109, 101, 105, 4, 15),
        _candle(103, 108, 102, 104, 4, 30),
    ]
    result = detector.detect(candles)
    assert not result.is_candidate
    assert not result.ch1_found
    assert not result.cl1_found


def test_only_ch1_found_no_cl1(detector):
    orb = _orb_candle(high=110, low=100)
    candles = [
        orb,
        _candle(108, 115, 107, 114, 4, 15),  # CH1 found (high=115 > 110)
        _candle(114, 118, 112, 117, 4, 30),  # Condition A met (close=117 > CH1_High=115)
        # CL1 never found — price stays above ORL
        _candle(115, 116, 110, 111, 5, 0),
    ]
    result = detector.detect(candles)
    assert result.ch1_found
    assert result.condition_a_met
    assert not result.cl1_found
    assert not result.condition_b_met
    assert not result.is_candidate
    assert "CL1 not found" in (result.rejection_reason or "")


def test_only_cl1_found_no_ch1(detector):
    orb = _orb_candle(high=110, low=100)
    candles = [
        orb,
        _candle(102, 109, 95, 94, 4, 15),   # CL1 found (low=95 < 100)
        _candle(94, 98, 90, 89, 4, 30),     # Condition B met (close=89 < 95)
        # CH1 never found
        _candle(91, 99, 89, 92, 5, 0),
    ]
    result = detector.detect(candles)
    assert result.cl1_found
    assert result.condition_b_met
    assert not result.ch1_found
    assert not result.is_candidate
    assert "CH1 not found" in (result.rejection_reason or "")


# ── Confirmation conditions ───────────────────────────────────────────────────

def test_ch1_and_cl1_found_but_no_confirmation(detector):
    orb = _orb_candle(high=110, low=100)
    candles = [
        orb,
        _candle(108, 115, 95, 108, 4, 15),  # CH1 (high=115) AND CL1 (low=95) on same candle
        # No close above 115 and no close below 95 in remaining candles
        _candle(108, 112, 96, 109, 4, 30),
        _candle(109, 110, 98, 105, 5, 0),
    ]
    result = detector.detect(candles)
    assert result.ch1_found
    assert result.cl1_found
    assert not result.condition_a_met
    assert not result.condition_b_met
    assert not result.is_candidate


def test_condition_a_only_no_condition_b(detector):
    orb = _orb_candle(high=110, low=100)
    candles = [
        orb,
        _candle(108, 115, 101, 110, 4, 15),  # CH1 found (high=115)
        _candle(113, 120, 108, 116, 4, 30),  # Condition A met (close=116 > ch1_high=115)
        # CL1 never found
        _candle(112, 118, 105, 110, 5, 0),
    ]
    result = detector.detect(candles)
    assert result.condition_a_met
    assert not result.is_candidate


# ── Full candidate detection ──────────────────────────────────────────────────

def test_both_conditions_met_is_candidate(detector):
    orb = _orb_candle(high=110, low=100)
    candles = [
        orb,
        _candle(108, 115, 99, 114, 4, 15),   # CH1 (high=115>110), CL1 (low=99<100)
        _candle(114, 120, 97, 116, 4, 30),   # Cond A: close=116 > ch1_high=115 ✓
                                              # Cond B: close=116 NOT < cl1_low=99 ✗
        _candle(110, 112, 88, 87, 5, 0),     # Cond B: close=87 < cl1_low=99 ✓
    ]
    result = detector.detect(candles)
    assert result.is_candidate
    assert result.ch1_found
    assert result.cl1_found
    assert result.condition_a_met
    assert result.condition_b_met
    assert result.ch1_high == 115.0
    assert result.cl1_low == 99.0
    assert result.rejection_reason is None


def test_conditions_met_in_different_candles(detector):
    """Conditions A and B may be confirmed by different subsequent candles."""
    orb = _orb_candle(high=110, low=100)
    candles = [
        orb,
        _candle(106, 115, 101, 113, 4, 15),  # CH1 (high=115)
        _candle(108, 109, 95, 108, 4, 30),   # CL1 (low=95)
        _candle(111, 117, 94, 116, 5, 0),    # Cond A (close=116>115) + Cond B (close=116 NOT<95)
        _candle(108, 112, 87, 86, 5, 15),    # Cond B (close=86<95) ✓
    ]
    result = detector.detect(candles)
    assert result.is_candidate
    assert result.condition_a_met
    assert result.condition_b_met


def test_orb_values_correct(detector):
    orb = _orb_candle(high=200.0, low=190.0)
    candles = [
        orb,
        _candle(196, 205, 185, 204, 4, 15),  # CH1+CL1
        _candle(204, 210, 183, 206, 4, 30),  # Cond A
        _candle(200, 202, 177, 176, 5, 0),   # Cond B
    ]
    result = detector.detect(candles)
    assert result.orh_d == 200.0
    assert result.orl_d == 190.0
    assert result.ch1_high == 205.0
    assert result.cl1_low == 185.0
    assert result.is_candidate


def test_candle_count_is_correct(detector):
    orb = _orb_candle()
    # Use valid minute values (0, 15, 30, 45)
    extra = [_candle(105, 115, 95, 116, 4 + i // 4, (i % 4) * 15) for i in range(1, 5)]
    candles = [orb] + extra
    result = detector.detect(candles)
    assert result.candle_count == len(candles)


def test_condition_candle_must_be_after_ch1(detector):
    """The CH1 candle itself cannot satisfy Condition A (its high == CH1_High, strict >)."""
    orb = _orb_candle(high=110, low=100)
    # CH1 candle has high=115 — equals CH1_High, so strict ">" excludes it.
    candles = [
        orb,
        _candle(108, 115, 95, 113, 4, 15),  # CH1 (high=115); CL1 (low=95)
        # No subsequent candle trades above 115
        _candle(110, 112, 96, 111, 4, 30),
        # Condition B only (low 88 < 95)
        _candle(105, 108, 88, 87, 5, 0),
    ]
    result = detector.detect(candles)
    # Condition A not met (no candle AFTER CH1 has a high above 115)
    assert not result.condition_a_met
    assert result.condition_b_met
    assert not result.is_candidate


def test_conditions_met_on_touch_without_close(detector):
    """Touch-based: a later candle that wicks past CH1_High / CL1_Low but closes
    back inside still satisfies Conditions A and B."""
    orb = _orb_candle(high=110, low=100)
    candles = [
        orb,
        _candle(108, 115, 99, 108, 4, 15),   # CH1 (high=115>110); CL1 (low=99<100)
        # Cond A: high=120 > 115 but close=113 (< 115) → would FAIL old close-based rule
        _candle(114, 120, 112, 113, 4, 30),
        # Cond B: low=90 < 99 but close=105 (> 99) → would FAIL old close-based rule
        _candle(110, 112, 90, 105, 5, 0),
    ]
    result = detector.detect(candles)
    assert result.ch1_found
    assert result.cl1_found
    assert result.condition_a_met
    assert result.condition_b_met
    assert result.is_candidate
    # Confirmation values now record the touching high/low, not the close
    assert result.condition_a_close == 120.0
    assert result.condition_b_close == 90.0
