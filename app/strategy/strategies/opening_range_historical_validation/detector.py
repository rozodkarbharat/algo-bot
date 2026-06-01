"""
ORHV Phase 1 — Setup Detection.

Pure Python — NO database calls, NO I/O.
Receives a single trading day's sorted 15-minute candles and determines
whether the day qualifies as an ORHV setup candidate.

Detection rules (applied strictly on CLOSED candles):

  Step 0 — Opening Range
    ORH_D = first candle (9:15–9:30 IST) HIGH
    ORL_D = first candle LOW

  Step 1 — Find CH1
    CH1 = first subsequent candle whose HIGH > ORH_D
    CH1_High = CH1.high

  Step 2 — Find CL1
    CL1 = first subsequent candle whose LOW < ORL_D
    CL1_Low = CL1.low

  Step 3 — Condition A (close-based, no look-ahead bias)
    Any candle AFTER CH1 whose CLOSE > CH1_High

  Step 4 — Condition B (close-based, no look-ahead bias)
    Any candle AFTER CL1 whose CLOSE < CL1_Low

  Candidate = Condition A met AND Condition B met

Note on look-ahead:
  CH1/CL1 are identified by their HIGH/LOW (intra-bar touch is valid —
  this represents a market-order fill at the breakout level which a trader
  would observe in real-time as the candle's printed high).
  Conditions A and B require a CLOSE confirmation — this avoids acting on
  an intra-bar wick that could reverse before the candle closes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.models.historical_candle import CandleData
from app.strategy.strategies.opening_range_historical_validation.constants import (
    ORB_CLOSE_UTC_HOUR,
    ORB_CLOSE_UTC_MINUTE,
    ORB_OPEN_UTC_HOUR,
    ORB_OPEN_UTC_MINUTE,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class ORHVDetectionResult:
    """
    Immutable result from ORHVSetupDetector.detect().

    All fields are always populated — None values indicate a concept
    that does not apply (e.g. ch1_high is None when CH1 was not found).
    """

    # ── ORB ───────────────────────────────────────────────────────────────────
    orh_d: float
    orl_d: float

    # ── CH1 ───────────────────────────────────────────────────────────────────
    ch1_found: bool
    ch1_high: Optional[float]
    ch1_time: Optional[datetime]

    # ── CL1 ───────────────────────────────────────────────────────────────────
    cl1_found: bool
    cl1_low: Optional[float]
    cl1_time: Optional[datetime]

    # ── Confirmation ──────────────────────────────────────────────────────────
    condition_a_met: bool
    condition_a_time: Optional[datetime]
    condition_a_close: Optional[float]

    condition_b_met: bool
    condition_b_time: Optional[datetime]
    condition_b_close: Optional[float]

    # ── Verdict ───────────────────────────────────────────────────────────────
    is_candidate: bool
    rejection_reason: Optional[str]
    candle_count: int


class ORHVSetupDetector:
    """
    Detects ORHV setup patterns from a single trading day's 15-min candles.

    Usage:
        detector = ORHVSetupDetector()
        result = detector.detect(candles)
        if result.is_candidate:
            # proceed to Phase 2 validation
    """

    # ── Public API ────────────────────────────────────────────────────────────

    def detect(self, candles: list[CandleData]) -> ORHVDetectionResult:
        """
        Run Phase 1 detection on a full trading day's sorted candle list.

        Args:
            candles: All 15-min candles for ONE trading day, sorted
                     chronologically oldest-first.
                     candles[0] MUST be the 9:15 IST opening candle.

        Returns:
            ORHVDetectionResult with all fields populated.
        """
        if not candles:
            return self._reject("No candles provided.", orh_d=0.0, orl_d=0.0)

        # ── Step 0: Opening Range ─────────────────────────────────────────────
        first = candles[0]

        if first.high < first.low:
            return self._reject(
                "First candle high < low (data error).",
                orh_d=first.high, orl_d=first.low,
            )

        orh_d = first.high
        orl_d = first.low

        rest = candles[1:]
        if not rest:
            return self._reject(
                "Only the ORB candle available; need at least one subsequent candle.",
                orh_d=orh_d, orl_d=orl_d,
            )

        # ── Steps 1 & 2: Find CH1 and CL1 ────────────────────────────────────
        ch1_candle: Optional[CandleData] = None
        cl1_candle: Optional[CandleData] = None

        for c in rest:
            if ch1_candle is None and c.high > orh_d:
                ch1_candle = c
            if cl1_candle is None and c.low < orl_d:
                cl1_candle = c
            # Stop early once both are found
            if ch1_candle is not None and cl1_candle is not None:
                break

        ch1_found = ch1_candle is not None
        cl1_found = cl1_candle is not None
        ch1_high = ch1_candle.high if ch1_found else None
        ch1_time = ch1_candle.time if ch1_found else None
        cl1_low = cl1_candle.low if cl1_found else None
        cl1_time = cl1_candle.time if cl1_found else None

        # If either leg is missing, it cannot be a candidate
        if not ch1_found and not cl1_found:
            return ORHVDetectionResult(
                orh_d=orh_d, orl_d=orl_d,
                ch1_found=False, ch1_high=None, ch1_time=None,
                cl1_found=False, cl1_low=None, cl1_time=None,
                condition_a_met=False, condition_a_time=None, condition_a_close=None,
                condition_b_met=False, condition_b_time=None, condition_b_close=None,
                is_candidate=False,
                rejection_reason="Neither ORH_D nor ORL_D was breached by any candle.",
                candle_count=len(candles),
            )

        # ── Steps 3 & 4: Condition A and B (close-based) ──────────────────────
        cond_a_time: Optional[datetime] = None
        cond_a_close: Optional[float] = None
        cond_b_time: Optional[datetime] = None
        cond_b_close: Optional[float] = None

        # For Condition A we scan candles AFTER CH1 (not CH1 itself — close can't > own high)
        # For Condition B we scan candles AFTER CL1
        ch1_idx = rest.index(ch1_candle) if ch1_found else len(rest)
        cl1_idx = rest.index(cl1_candle) if cl1_found else len(rest)

        for i, c in enumerate(rest):
            # Condition A: candle strictly after CH1 closes above CH1_High
            if (
                ch1_found
                and cond_a_close is None
                and i > ch1_idx
                and c.close > ch1_high  # type: ignore[operator]
            ):
                cond_a_time = c.time
                cond_a_close = c.close

            # Condition B: candle strictly after CL1 closes below CL1_Low
            if (
                cl1_found
                and cond_b_close is None
                and i > cl1_idx
                and c.close < cl1_low  # type: ignore[operator]
            ):
                cond_b_time = c.time
                cond_b_close = c.close

        condition_a_met = cond_a_close is not None
        condition_b_met = cond_b_close is not None
        is_candidate = condition_a_met and condition_b_met

        # Build rejection reason for diagnostics
        rejection_reason: Optional[str] = None
        if not is_candidate:
            parts = []
            if not ch1_found:
                parts.append("CH1 not found (ORH_D never breached)")
            elif not condition_a_met:
                parts.append("Condition A not met (no close above CH1_High)")
            if not cl1_found:
                parts.append("CL1 not found (ORL_D never breached)")
            elif not condition_b_met:
                parts.append("Condition B not met (no close below CL1_Low)")
            rejection_reason = "; ".join(parts) if parts else "Conditions not met."

        if is_candidate:
            logger.debug(
                "[ORHV] Candidate: ORH_D=%.2f ORL_D=%.2f CH1_H=%.2f CL1_L=%.2f",
                orh_d, orl_d, ch1_high, cl1_low,
            )

        return ORHVDetectionResult(
            orh_d=orh_d,
            orl_d=orl_d,
            ch1_found=ch1_found,
            ch1_high=ch1_high,
            ch1_time=ch1_time,
            cl1_found=cl1_found,
            cl1_low=cl1_low,
            cl1_time=cl1_time,
            condition_a_met=condition_a_met,
            condition_a_time=cond_a_time,
            condition_a_close=cond_a_close,
            condition_b_met=condition_b_met,
            condition_b_time=cond_b_time,
            condition_b_close=cond_b_close,
            is_candidate=is_candidate,
            rejection_reason=rejection_reason,
            candle_count=len(candles),
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _reject(reason: str, orh_d: float, orl_d: float) -> ORHVDetectionResult:
        return ORHVDetectionResult(
            orh_d=orh_d, orl_d=orl_d,
            ch1_found=False, ch1_high=None, ch1_time=None,
            cl1_found=False, cl1_low=None, cl1_time=None,
            condition_a_met=False, condition_a_time=None, condition_a_close=None,
            condition_b_met=False, condition_b_time=None, condition_b_close=None,
            is_candidate=False,
            rejection_reason=reason,
            candle_count=0,
        )


# ── Module-level default instance (stateless) ─────────────────────────────────

default_detector = ORHVSetupDetector()
