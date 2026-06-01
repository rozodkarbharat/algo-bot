"""
Strategy Health Score Engine.

Computes a composite 0-100 health score for a trading strategy over a
given date range, using four equally-weighted dimensions:

  1. Signal Quality   — what fraction of generated signals actually execute
  2. Execution Quality — average entry slippage vs. an acceptable threshold
  3. PnL Stability    — win rate and profit factor weighted blend
  4. Slippage Cost    — slippage as a fraction of deployed capital

Each dimension is scored 0-100 and rolled into a single weighted overall
score. A letter grade (A-F), a confidence tier (HIGH/MEDIUM/LOW), and a
one-line recommendation are also returned.

Typical usage
-------------
    engine = HealthScoreEngine()
    result = await engine.compute(
        strategy_id="one_side_orb",
        from_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        to_date=datetime(2024, 3, 31, tzinfo=timezone.utc),
    )
    print(result.overall_score, result.grade, result.recommendation)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from beanie.operators import GTE, In, LTE

from app.models.live_signal import LiveSignal
from app.models.paper_trade import PaperTrade
from app.utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HealthDimension:
    """Score breakdown for a single health dimension."""

    name: str
    score: float          # 0-100, raw dimension score
    weight: float         # fraction of overall score (e.g. 0.25)
    weighted_score: float # score * weight
    detail: str           # human-readable explanation


@dataclass(frozen=True)
class HealthScoreResult:
    """Aggregate health score result for a strategy over a period."""

    overall_score: float            # 0-100, weighted sum across dimensions
    grade: str                      # A / B / C / D / F
    signal_quality_score: float     # 0-100
    execution_quality_score: float  # 0-100
    pnl_stability_score: float      # 0-100
    slippage_score: float           # 0-100
    dimensions: list[HealthDimension]
    confidence: str                 # HIGH / MEDIUM / LOW
    strategy_id: str
    sample_trades: int
    recommendation: str


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class HealthScoreEngine:
    """
    Compute a 0-100 health score for a given strategy over a date window.

    All four dimensions carry equal weight (25 % each).
    """

    # Dimension weights — must sum to 1.0
    SIGNAL_QUALITY_WEIGHT: float = 0.25
    EXECUTION_QUALITY_WEIGHT: float = 0.25
    PNL_STABILITY_WEIGHT: float = 0.25
    SLIPPAGE_WEIGHT: float = 0.25

    # Slippage thresholds
    _MAX_ACCEPTABLE_BPS: float = 50.0    # 0.5 % — execution quality ceiling
    _MAX_ACCEPTABLE_PCT: float = 0.2     # 0.2 % of deployed capital — slippage cost ceiling

    # Confidence thresholds
    _HIGH_CONFIDENCE_TRADES: int = 30
    _MEDIUM_CONFIDENCE_TRADES: int = 10

    async def compute(
        self,
        strategy_id: str,
        from_date: datetime,
        to_date: datetime,
    ) -> HealthScoreResult:
        """
        Compute the strategy health score for *strategy_id* between
        *from_date* and *to_date* (both UTC-aware).

        Returns a :class:`HealthScoreResult` containing per-dimension
        breakdowns, an overall score, a letter grade, and a recommendation.
        """
        logger.info(
            "health_score.compute | strategy=%s from=%s to=%s",
            strategy_id,
            from_date.isoformat(),
            to_date.isoformat(),
        )

        # ------------------------------------------------------------------ #
        # 1.  Fetch raw data
        # ------------------------------------------------------------------ #
        signals: list[LiveSignal] = await LiveSignal.find(
            LiveSignal.strategy_id == strategy_id,
            GTE(LiveSignal.trading_date, from_date),
            LTE(LiveSignal.trading_date, to_date),
        ).to_list()

        generated_count: int = len(signals)
        signal_ids: list[str] = [s.signal_id for s in signals]

        # Build a lookup: signal_id -> entry_price from the live signal
        signal_price_map: dict[str, float] = {
            s.signal_id: s.entry_price for s in signals
        }

        trades: list[PaperTrade] = []
        if signal_ids:
            trades = await PaperTrade.find(
                PaperTrade.strategy_id == strategy_id,
                In(PaperTrade.signal_id, signal_ids),
            ).to_list()

        executed_count: int = len(trades)
        sample_trades: int = executed_count

        logger.debug(
            "health_score | strategy=%s signals=%d trades=%d",
            strategy_id,
            generated_count,
            executed_count,
        )

        # ------------------------------------------------------------------ #
        # 2.  Signal Quality Score
        # ------------------------------------------------------------------ #
        sq_score, sq_detail = self._signal_quality(
            generated_count=generated_count,
            executed_count=executed_count,
        )

        # ------------------------------------------------------------------ #
        # 3.  Execution Quality Score
        # ------------------------------------------------------------------ #
        eq_score, eq_detail = self._execution_quality(
            trades=trades,
            signal_price_map=signal_price_map,
        )

        # ------------------------------------------------------------------ #
        # 4.  PnL Stability Score
        # ------------------------------------------------------------------ #
        ps_score, ps_detail = self._pnl_stability(trades=trades)

        # ------------------------------------------------------------------ #
        # 5.  Slippage Score
        # ------------------------------------------------------------------ #
        sl_score, sl_detail = self._slippage_cost(trades=trades)

        # ------------------------------------------------------------------ #
        # 6.  Weighted overall score
        # ------------------------------------------------------------------ #
        overall = (
            sq_score * self.SIGNAL_QUALITY_WEIGHT
            + eq_score * self.EXECUTION_QUALITY_WEIGHT
            + ps_score * self.PNL_STABILITY_WEIGHT
            + sl_score * self.SLIPPAGE_WEIGHT
        )

        dimensions: list[HealthDimension] = [
            HealthDimension(
                name="Signal Quality",
                score=sq_score,
                weight=self.SIGNAL_QUALITY_WEIGHT,
                weighted_score=sq_score * self.SIGNAL_QUALITY_WEIGHT,
                detail=sq_detail,
            ),
            HealthDimension(
                name="Execution Quality",
                score=eq_score,
                weight=self.EXECUTION_QUALITY_WEIGHT,
                weighted_score=eq_score * self.EXECUTION_QUALITY_WEIGHT,
                detail=eq_detail,
            ),
            HealthDimension(
                name="PnL Stability",
                score=ps_score,
                weight=self.PNL_STABILITY_WEIGHT,
                weighted_score=ps_score * self.PNL_STABILITY_WEIGHT,
                detail=ps_detail,
            ),
            HealthDimension(
                name="Slippage Cost",
                score=sl_score,
                weight=self.SLIPPAGE_WEIGHT,
                weighted_score=sl_score * self.SLIPPAGE_WEIGHT,
                detail=sl_detail,
            ),
        ]

        grade = self._grade(overall)
        confidence = self._confidence(sample_trades)
        recommendation = self._recommendation(
            overall_score=overall,
            sq_score=sq_score,
            eq_score=eq_score,
            ps_score=ps_score,
            sl_score=sl_score,
        )

        logger.info(
            "health_score.result | strategy=%s overall=%.1f grade=%s confidence=%s",
            strategy_id,
            overall,
            grade,
            confidence,
        )

        return HealthScoreResult(
            overall_score=round(overall, 2),
            grade=grade,
            signal_quality_score=round(sq_score, 2),
            execution_quality_score=round(eq_score, 2),
            pnl_stability_score=round(ps_score, 2),
            slippage_score=round(sl_score, 2),
            dimensions=dimensions,
            confidence=confidence,
            strategy_id=strategy_id,
            sample_trades=sample_trades,
            recommendation=recommendation,
        )

    # ---------------------------------------------------------------------- #
    # Dimension calculators (private helpers)
    # ---------------------------------------------------------------------- #

    def _signal_quality(
        self,
        *,
        generated_count: int,
        executed_count: int,
    ) -> tuple[float, str]:
        """
        Signal Quality Score.

        conversion_rate = executed / generated  (0 when generated == 0)
        score           = conversion_rate * 100
        """
        if generated_count == 0:
            score = 0.0
            detail = "No signals generated in the period."
        else:
            conversion_rate = executed_count / generated_count
            score = conversion_rate * 100.0
            detail = (
                f"{executed_count} of {generated_count} signals executed "
                f"({conversion_rate * 100:.1f}% conversion rate)."
            )

        return score, detail

    def _execution_quality(
        self,
        *,
        trades: list[PaperTrade],
        signal_price_map: dict[str, float],
    ) -> tuple[float, str]:
        """
        Execution Quality Score.

        For each trade: slippage_bps = |trade.entry_price - signal.entry_price|
                                       / signal.entry_price * 10_000
        avg_slippage_bps = mean of per-trade slippage_bps
        score = max(0, 100 - avg_slippage_bps / 50 * 100)
        """
        if not trades:
            return 50.0, "No paper trades available; using neutral score."

        bps_values: list[float] = []
        for trade in trades:
            if trade.signal_id and trade.signal_id in signal_price_map:
                signal_entry = signal_price_map[trade.signal_id]
                if signal_entry > 0:
                    bps = abs(trade.entry_price - signal_entry) / signal_entry * 10_000
                    bps_values.append(bps)

        if not bps_values:
            return 50.0, "Could not compute entry slippage (no matching signal prices)."

        avg_bps = sum(bps_values) / len(bps_values)
        score = max(0.0, 100.0 - (avg_bps / self._MAX_ACCEPTABLE_BPS * 100.0))
        detail = (
            f"Average entry slippage {avg_bps:.1f} bps across {len(bps_values)} trades "
            f"(threshold {self._MAX_ACCEPTABLE_BPS:.0f} bps)."
        )
        return score, detail

    def _pnl_stability(
        self,
        *,
        trades: list[PaperTrade],
    ) -> tuple[float, str]:
        """
        PnL Stability Score.

        win_rate       = count(pnl > 0) / total
        profit_factor  = sum(pnl > 0) / abs(sum(pnl < 0))  → 2.0 if no losses
        score          = min(100, win_rate * 60 + min(profit_factor, 2.0) * 20)
        """
        if not trades:
            return 50.0, "No paper trades available; using neutral score."

        total = len(trades)
        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl < 0]

        win_rate = len(wins) / total
        gross_profit = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))

        if gross_loss == 0:
            profit_factor = 2.0
        else:
            profit_factor = gross_profit / gross_loss

        score = min(100.0, win_rate * 60.0 + min(profit_factor, 2.0) * 20.0)
        detail = (
            f"Win rate {win_rate * 100:.1f}% ({len(wins)}/{total} trades), "
            f"profit factor {profit_factor:.2f}."
        )
        return score, detail

    def _slippage_cost(
        self,
        *,
        trades: list[PaperTrade],
    ) -> tuple[float, str]:
        """
        Slippage Cost Score.

        For each trade: slippage_pct = slippage / (entry_price * quantity) * 100
        avg_slippage_cost_pct = mean of per-trade slippage_pct
        score = max(0, 100 - avg_slippage_cost_pct / 0.2 * 100)
        """
        if not trades:
            return 50.0, "No paper trades available; using neutral score."

        pct_values: list[float] = []
        for trade in trades:
            deployed_capital = trade.entry_price * trade.quantity
            if deployed_capital > 0:
                pct = trade.slippage / deployed_capital * 100.0
                pct_values.append(pct)

        if not pct_values:
            return 50.0, "Could not compute slippage cost (missing quantity data)."

        avg_pct = sum(pct_values) / len(pct_values)
        score = max(0.0, 100.0 - (avg_pct / self._MAX_ACCEPTABLE_PCT * 100.0))
        detail = (
            f"Average slippage cost {avg_pct:.3f}% of deployed capital "
            f"(threshold {self._MAX_ACCEPTABLE_PCT:.1f}%)."
        )
        return score, detail

    # ---------------------------------------------------------------------- #
    # Grading and classification helpers
    # ---------------------------------------------------------------------- #

    @staticmethod
    def _grade(score: float) -> str:
        """Convert a 0-100 score to a letter grade."""
        if score >= 80:
            return "A"
        if score >= 65:
            return "B"
        if score >= 50:
            return "C"
        if score >= 35:
            return "D"
        return "F"

    def _confidence(self, sample_trades: int) -> str:
        """Confidence tier based on sample size."""
        if sample_trades >= self._HIGH_CONFIDENCE_TRADES:
            return "HIGH"
        if sample_trades >= self._MEDIUM_CONFIDENCE_TRADES:
            return "MEDIUM"
        return "LOW"

    @staticmethod
    def _recommendation(
        *,
        overall_score: float,
        sq_score: float,
        eq_score: float,
        ps_score: float,
        sl_score: float,
    ) -> str:
        """
        Return a one-line recommendation pointing at the weakest dimension.

        When overall score is acceptable (>= 65) and no individual dimension
        is critically low (< 50), return a positive message.
        """
        scores: dict[str, float] = {
            "signal_quality": sq_score,
            "execution_quality": eq_score,
            "pnl_stability": ps_score,
            "slippage": sl_score,
        }
        worst_dim = min(scores, key=lambda k: scores[k])
        worst_score = scores[worst_dim]

        # Only surface a specific recommendation when the weakest dimension
        # is genuinely problematic (< 50).
        if worst_score < 50.0:
            recommendations: dict[str, str] = {
                "signal_quality": (
                    "Investigate risk manager rejections — many signals not executing"
                ),
                "execution_quality": (
                    "High slippage detected — review order type and timing"
                ),
                "pnl_stability": (
                    "Win rate below expectations — review strategy parameters"
                ),
                "slippage": (
                    "Transaction costs eroding returns — consider tighter slippage controls"
                ),
            }
            return recommendations[worst_dim]

        return "Strategy performing within acceptable parameters"
