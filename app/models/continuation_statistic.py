"""
Continuation probability statistic document.

One document per symbol. Tracks how often a stock that was one-sided yesterday
is also one-sided today: P(OneSideToday | OneSideYesterday).

Updated nightly after the one-side day detection job runs.
Used by the shortlist service to filter tradable candidates.
"""

from datetime import datetime, timezone
from typing import Optional

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ContinuationStatistic(Document):
    """
    Per-symbol continuation probability computed from historical one-side days.

    Collection: continuation_statistics
    Unique constraint: symbol
    """

    symbol: str = Field(..., description="NSE ticker symbol")

    # Raw counts from the historical analysis
    total_occurrences: int = Field(
        default=0,
        description="Total number of one-side days in the lookback window",
    )
    continuation_successes: int = Field(
        default=0,
        description="Number of one-side days where the next trading day was ALSO one-sided",
    )
    continuation_failures: int = Field(
        default=0,
        description="Number of one-side days where the next trading day was NOT one-sided",
    )

    # Derived probability — range 0.0 to 1.0
    continuation_probability: float = Field(
        default=0.0,
        description="P(OneSideToday | OneSideYesterday) as a fraction 0.0–1.0",
    )

    # Tradable flag — set when probability >= threshold AND sample size is sufficient
    tradable: bool = Field(
        default=False,
        description="True when continuation_probability >= configured threshold and min occurrences met",
    )

    # Configuration snapshot stored at calculation time for reproducibility
    lookback_days: int = Field(
        default=252,
        description="Lookback window used for the last calculation (trading days)",
    )
    min_occurrences_required: int = Field(
        default=10,
        description="Minimum one-side occurrences required before tradable can be True",
    )
    probability_threshold: float = Field(
        default=0.70,
        description="Probability threshold above which tradable=True",
    )

    last_calculated_at: Optional[datetime] = Field(
        default=None,
        description="When continuation stats were last computed",
    )
    metadata: dict = Field(default_factory=dict)

    class Settings:
        name = "continuation_statistics"
        indexes = [
            IndexModel([("symbol", ASCENDING)], unique=True, name="symbol_unique"),
            IndexModel([("tradable", ASCENDING)]),
            IndexModel([("continuation_probability", DESCENDING)]),
        ]

    def recalculate(
        self,
        total: int,
        successes: int,
        lookback_days: int,
        min_occurrences: int,
        threshold: float,
    ) -> None:
        """Update all derived fields from raw counts."""
        self.total_occurrences = total
        self.continuation_successes = successes
        self.continuation_failures = total - successes
        self.continuation_probability = (successes / total) if total > 0 else 0.0
        self.tradable = (
            total >= min_occurrences and self.continuation_probability >= threshold
        )
        self.lookback_days = lookback_days
        self.min_occurrences_required = min_occurrences
        self.probability_threshold = threshold
        self.last_calculated_at = _utcnow()
