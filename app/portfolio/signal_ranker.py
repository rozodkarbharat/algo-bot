"""
Signal ranking engine.

Computes a composite ranking_score ∈ [0.0, 1.0] for a single live signal
using five normalised factors drawn from the strategy's historical record
and the signal's own probability data.

Factors (configurable weights, must sum to 1.0):
  1. win_rate            — historical win rate for the symbol (from StockPerformanceAnalytics)
  2. expectancy          — expected value per trade, normalised to [0, 1]
  3. probability_score   — continuation probability supplied by the shortlist engine
  4. stock_reliability   — continuation_probability from ContinuationStatistic
  5. drawdown_penalty    — 1 - normalised_max_drawdown (lower drawdown = higher score)

When historical data is unavailable for a factor, the factor defaults to 0.5
(neutral) so signals from new strategies still receive a sensible score.

This module is pure (no async / no I/O). All inputs come from the service
layer which fetches the required documents before calling `rank_signal()`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)

# ── Default factor weights ────────────────────────────────────────────────────
# Must sum to 1.0.  Override per-instance for strategy-specific tuning.
DEFAULT_WEIGHTS: dict[str, float] = {
    "win_rate": 0.25,
    "expectancy": 0.25,
    "probability_score": 0.25,
    "stock_reliability": 0.15,
    "drawdown_penalty": 0.10,
}

# Normalisation reference for expectancy: values above this ceiling are
# clamped to 1.0. ₹5 000 per trade expected value = top-tier signal.
_EXPECTANCY_CEILING: float = 5_000.0

# Normalisation reference for max_drawdown: drawdowns above this floor are
# clamped to 0.0 (worst possible drawdown_penalty factor).
_DRAWDOWN_FLOOR: float = -100_000.0


# ── Input / output types ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class SignalRankInput:
    """
    Caller-supplied context for ranking a single signal.

    All numeric fields are Optional — the ranker gracefully handles missing
    data by substituting neutral (0.5) values.
    """

    symbol: str
    strategy_id: str
    probability_score: Optional[float]       # [0.0, 1.0] from shortlist engine

    # From StockPerformanceAnalytics (may be None if no research run yet)
    historical_win_rate: Optional[float]     # [0.0, 1.0]
    historical_expectancy: Optional[float]   # ₹ value; can be negative
    historical_max_drawdown: Optional[float] # ₹ value; ≤ 0

    # From ContinuationStatistic (may be None for new symbols)
    continuation_probability: Optional[float]  # [0.0, 1.0]


@dataclass(frozen=True)
class RankResult:
    """Output of the ranking engine for one signal."""

    ranking_score: float                   # composite [0.0, 1.0]
    components: dict[str, float]           # per-factor scores before weighting
    weighted_components: dict[str, float]  # per-factor contribution to final score


# ── Ranker ────────────────────────────────────────────────────────────────────

class SignalRanker:
    """
    Stateless signal ranking engine.

    `weights` must be a dict whose values sum to 1.0. Provide a custom dict
    to override the default factor weights without subclassing.
    """

    def __init__(self, weights: Optional[dict[str, float]] = None) -> None:
        self._weights = weights or DEFAULT_WEIGHTS.copy()
        _validate_weights(self._weights)

    # ── Public API ────────────────────────────────────────────────────────────

    def rank(self, inp: SignalRankInput) -> RankResult:
        """
        Compute the composite ranking score for a single signal.

        Each factor is normalised to [0, 1].  Unknown values default to 0.5.
        Returns a `RankResult` with the final score and per-factor breakdown.
        """
        components: dict[str, float] = {
            "win_rate": self._normalise_win_rate(inp.historical_win_rate),
            "expectancy": self._normalise_expectancy(inp.historical_expectancy),
            "probability_score": self._normalise_probability(inp.probability_score),
            "stock_reliability": self._normalise_probability(inp.continuation_probability),
            "drawdown_penalty": self._normalise_drawdown(inp.historical_max_drawdown),
        }

        weighted: dict[str, float] = {
            factor: round(score * self._weights.get(factor, 0.0), 6)
            for factor, score in components.items()
        }

        ranking_score = round(sum(weighted.values()), 6)
        # Clamp to [0, 1] to guard against floating-point drift.
        ranking_score = max(0.0, min(1.0, ranking_score))

        logger.debug(
            "[ranker] %s/%s score=%.4f components=%s",
            inp.strategy_id,
            inp.symbol,
            ranking_score,
            components,
        )
        return RankResult(
            ranking_score=ranking_score,
            components=components,
            weighted_components=weighted,
        )

    # ── Factor normalisers ────────────────────────────────────────────────────

    @staticmethod
    def _normalise_win_rate(value: Optional[float]) -> float:
        if value is None:
            return 0.5
        return max(0.0, min(1.0, float(value)))

    @staticmethod
    def _normalise_probability(value: Optional[float]) -> float:
        if value is None:
            return 0.5
        return max(0.0, min(1.0, float(value)))

    @staticmethod
    def _normalise_expectancy(value: Optional[float]) -> float:
        if value is None:
            return 0.5
        if value <= 0:
            # Negative expectancy: map [-∞, 0] → [0.0, 0.5]
            # Use ₹-5 000 as the worst-case reference point.
            ratio = value / abs(_DRAWDOWN_FLOOR)
            return max(0.0, 0.5 + ratio * 0.5)
        # Positive expectancy: map [0, ceiling] → [0.5, 1.0]
        ratio = min(1.0, value / _EXPECTANCY_CEILING)
        return 0.5 + ratio * 0.5

    @staticmethod
    def _normalise_drawdown(value: Optional[float]) -> float:
        """Lower (more negative) drawdown → lower score."""
        if value is None:
            return 0.5
        # value is ≤ 0 (a loss in ₹).
        # Map [floor, 0] → [0.0, 1.0]; clamp anything worse than floor to 0.
        if value >= 0:
            return 1.0
        ratio = value / _DRAWDOWN_FLOOR  # 0.0 .. 1.0 (floor → ceiling)
        return max(0.0, min(1.0, 1.0 - ratio))

    @property
    def weights(self) -> dict[str, float]:
        return dict(self._weights)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _validate_weights(weights: dict[str, float]) -> None:
    total = round(sum(weights.values()), 6)
    if abs(total - 1.0) > 1e-4:
        raise ValueError(
            f"Signal ranker weights must sum to 1.0, got {total}. "
            f"Weights: {weights}"
        )


def rank_signals(
    inputs: list[SignalRankInput],
    weights: Optional[dict[str, float]] = None,
) -> list[tuple[SignalRankInput, RankResult]]:
    """
    Rank a batch of signals and return them sorted by descending score.

    Convenience function for the portfolio service which needs to rank all
    signals arriving in a session window before allocating capital.
    """
    ranker = SignalRanker(weights=weights)
    results = [(inp, ranker.rank(inp)) for inp in inputs]
    results.sort(key=lambda pair: pair[1].ranking_score, reverse=True)
    return results
