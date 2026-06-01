"""
Continuation probability engine.

Pure strategy logic — NO database calls, NO broker imports.
Receives a sorted history of (trading_date, is_one_side) pairs and computes:

    P(OneSideToday | OneSideYesterday)

Meaning: Of all historical one-side days, how often was the NEXT trading day
also a one-side day?

Direction is intentionally ignored — only "was the day one-sided?" matters
for the continuation statistics.

Scalability:
  - Stateless pure function — safe for batch/parallel backtest processing.
  - Can process 5+ years of 50-stock data in milliseconds (no I/O).
  - Thread-pool safe; no shared mutable state.
"""

from dataclasses import dataclass
from datetime import date
from typing import Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class ContinuationAnalysisResult:
    """
    Result returned by ContinuationProbabilityEngine.calculate().

    All counts reference the analysed lookback window only.
    """

    symbol: str

    # Raw counts
    total_occurrences: int       # number of one-side days found in window
    continuation_successes: int  # one-side days where next day was ALSO one-side
    continuation_failures: int   # one-side days where next day was NOT one-side

    # Derived probability — range 0.0 to 1.0
    continuation_probability: float

    # Tradability verdict
    tradable: bool

    # Configuration used for this calculation
    lookback_days: int
    min_occurrences: int
    probability_threshold: float

    # Diagnostics
    total_days_analyzed: int     # total calendar entries processed
    rejection_reason: Optional[str]  # reason tradable=False, or None if tradable


class ContinuationProbabilityEngine:
    """
    Computes P(OneSideToday | OneSideYesterday) from a historical series.

    Usage:
        engine = ContinuationProbabilityEngine(
            lookback_days=252,
            min_occurrences=10,
            probability_threshold=0.70,
        )
        result = engine.calculate(symbol="RELIANCE", history=[(date1, True), (date2, False), ...])
        if result.tradable:
            # stock is a valid candidate for the shortlist
    """

    def __init__(
        self,
        lookback_days: int = 252,
        min_occurrences: int = 10,
        probability_threshold: float = 0.70,
    ) -> None:
        """
        Args:
            lookback_days: Maximum number of trading days to look back.
                           252 ≈ 1 calendar year, 1260 ≈ 5 years.
            min_occurrences: Minimum number of one-side occurrences required
                             before tradable can be True. Prevents noise from
                             symbols with very few historical OSD days.
            probability_threshold: Probability (0.0–1.0) above which the stock
                                    is considered a tradable continuation candidate.
        """
        self.lookback_days = lookback_days
        self.min_occurrences = min_occurrences
        self.probability_threshold = probability_threshold

    # ── Public interface ──────────────────────────────────────────────────────

    def calculate(
        self,
        symbol: str,
        history: list[tuple[date, bool]],
    ) -> ContinuationAnalysisResult:
        """
        Calculate continuation probability from a chronological history.

        Args:
            symbol: Ticker symbol (used only for logging/result labelling).
            history: List of (trading_date, is_one_side) tuples, sorted
                     chronologically oldest-first. Dates must be consecutive
                     trading days (gaps are handled gracefully).

        Returns:
            ContinuationAnalysisResult with all fields populated.
        """
        if not history:
            return self._no_data(symbol, reason="No historical data provided.")

        # Apply lookback window — keep only the most recent N trading days.
        window = history[-self.lookback_days :] if len(history) > self.lookback_days else history

        if len(window) < 2:
            return self._no_data(
                symbol,
                total_days=len(window),
                reason=f"Only {len(window)} day(s) in lookback window; need at least 2.",
            )

        # ── Core calculation ──────────────────────────────────────────────────
        # For each consecutive pair (day_i, day_i+1):
        #   If day_i was one-side → check if day_i+1 was also one-side.
        total_occurrences = 0
        continuation_successes = 0

        for i in range(len(window) - 1):
            _today_date, today_is_osd = window[i]
            _next_date, next_is_osd = window[i + 1]

            if today_is_osd:
                total_occurrences += 1
                if next_is_osd:
                    continuation_successes += 1

        continuation_failures = total_occurrences - continuation_successes
        probability = (
            continuation_successes / total_occurrences if total_occurrences > 0 else 0.0
        )

        # ── Tradability verdict ───────────────────────────────────────────────
        tradable = False
        rejection_reason: Optional[str] = None

        if total_occurrences < self.min_occurrences:
            rejection_reason = (
                f"Only {total_occurrences} one-side occurrence(s); "
                f"need >= {self.min_occurrences} for reliable statistics."
            )
        elif probability < self.probability_threshold:
            rejection_reason = (
                f"Probability {probability:.1%} < threshold {self.probability_threshold:.1%}."
            )
        else:
            tradable = True

        logger.debug(
            "[%s] Continuation: %d/%d occurrences, p=%.1f%%, tradable=%s",
            symbol,
            continuation_successes,
            total_occurrences,
            probability * 100,
            tradable,
        )

        return ContinuationAnalysisResult(
            symbol=symbol,
            total_occurrences=total_occurrences,
            continuation_successes=continuation_successes,
            continuation_failures=continuation_failures,
            continuation_probability=round(probability, 6),
            tradable=tradable,
            lookback_days=self.lookback_days,
            min_occurrences=self.min_occurrences,
            probability_threshold=self.probability_threshold,
            total_days_analyzed=len(window),
            rejection_reason=rejection_reason,
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    def _no_data(
        self,
        symbol: str,
        total_days: int = 0,
        reason: str = "No data.",
    ) -> ContinuationAnalysisResult:
        return ContinuationAnalysisResult(
            symbol=symbol,
            total_occurrences=0,
            continuation_successes=0,
            continuation_failures=0,
            continuation_probability=0.0,
            tradable=False,
            lookback_days=self.lookback_days,
            min_occurrences=self.min_occurrences,
            probability_threshold=self.probability_threshold,
            total_days_analyzed=total_days,
            rejection_reason=reason,
        )


# ── Module-level default instance ────────────────────────────────────────────

default_probability_engine = ContinuationProbabilityEngine()
