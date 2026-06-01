"""
Walk-Forward Aggregator for the One-Side ORB strategy.

Pure Python — NO database calls, NO broker imports, NO I/O.
Combines all out-of-sample SegmentResult records into a single
AggregatedResult that summarises strategy performance across the
full walk-forward period.

Design notes:
- Failed segments (SegmentResult.error is not None) are excluded from all
  metric calculations but are counted in failed_segments.
- An empty or all-failed segment list returns a zeroed AggregatedResult.
- All arithmetic uses the standard library only (no pandas / numpy).
"""

import statistics
from dataclasses import dataclass, field

from app.research.walk_forward.engine import SegmentResult
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class AggregatedResult:
    """
    Combined out-of-sample performance across all walk-forward windows.

    Segment-level lists (segment_pnls, etc.) are in chronological window order
    and include only completed (non-failed) segments.
    """

    total_segments: int = 0
    completed_segments: int = 0
    failed_segments: int = 0

    # Trade-level aggregates
    total_trades: int = 0
    total_pnl: float = 0.0
    overall_win_rate: float = 0.0       # total wins / total trades

    # Segment-level averages
    avg_sharpe: float = 0.0             # mean Sharpe across completed segments
    avg_drawdown: float = 0.0           # mean max_drawdown across completed segments
    avg_profit_factor: float = 0.0      # mean profit_factor across completed segments

    # Extreme windows
    best_segment: int = 0               # segment_number with highest PnL
    worst_segment: int = 0              # segment_number with lowest PnL

    # Walk-forward win rate
    walk_forward_win_rate: float = 0.0  # fraction of completed segments with positive PnL

    # Per-segment series (for trend/regime analysis)
    segment_pnls: list[float] = field(default_factory=list)
    segment_win_rates: list[float] = field(default_factory=list)
    segment_sharpes: list[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total_segments": self.total_segments,
            "completed_segments": self.completed_segments,
            "failed_segments": self.failed_segments,
            "total_trades": self.total_trades,
            "total_pnl": self.total_pnl,
            "overall_win_rate": self.overall_win_rate,
            "avg_sharpe": self.avg_sharpe,
            "avg_drawdown": self.avg_drawdown,
            "avg_profit_factor": self.avg_profit_factor,
            "best_segment": self.best_segment,
            "worst_segment": self.worst_segment,
            "walk_forward_win_rate": self.walk_forward_win_rate,
            "segment_pnls": self.segment_pnls,
            "segment_win_rates": self.segment_win_rates,
            "segment_sharpes": self.segment_sharpes,
        }


# ── Aggregator ────────────────────────────────────────────────────────────────

class WalkForwardAggregator:
    """
    Combines all OOS SegmentResult records into a single AggregatedResult.

    Usage:
        aggregator = WalkForwardAggregator()
        agg = aggregator.aggregate(wf_engine_result.segments)
    """

    def aggregate(self, segments: list[SegmentResult]) -> AggregatedResult:
        """
        Compute aggregate statistics from a list of SegmentResult.

        Failed segments (error is not None) are excluded from metric
        calculations but counted in failed_segments.

        Args:
            segments: All SegmentResult records from WalkForwardEngine.run().

        Returns:
            Fully populated AggregatedResult.
        """
        result = AggregatedResult(total_segments=len(segments))

        if not segments:
            logger.warning("WalkForwardAggregator: received empty segment list.")
            return result

        # Partition into completed vs failed
        completed = [s for s in segments if s.error is None]
        failed    = [s for s in segments if s.error is not None]

        result.completed_segments = len(completed)
        result.failed_segments    = len(failed)

        if not completed:
            logger.warning(
                "WalkForwardAggregator: all %d segments failed — returning zeroed result.",
                len(segments),
            )
            return result

        # ── Trade-level aggregates ─────────────────────────────────────────────
        all_trades = [t for seg in completed for t in seg.oos_trades]
        result.total_trades = len(all_trades)
        result.total_pnl    = round(sum(t.pnl for t in all_trades), 2)

        winning_trades = [t for t in all_trades if t.pnl > 0]
        result.overall_win_rate = (
            round(len(winning_trades) / len(all_trades), 4) if all_trades else 0.0
        )

        # ── Segment-level series ───────────────────────────────────────────────
        result.segment_pnls       = [seg.oos_metrics.total_pnl for seg in completed]
        result.segment_win_rates  = [seg.oos_metrics.win_rate   for seg in completed]
        result.segment_sharpes    = [
            seg.oos_metrics.sharpe_ratio or 0.0 for seg in completed
        ]

        # ── Segment-level averages ─────────────────────────────────────────────
        result.avg_sharpe = round(
            statistics.mean(result.segment_sharpes), 4
        ) if result.segment_sharpes else 0.0

        drawdowns = [seg.oos_metrics.max_drawdown for seg in completed]
        result.avg_drawdown = round(statistics.mean(drawdowns), 2) if drawdowns else 0.0

        profit_factors = [seg.oos_metrics.profit_factor for seg in completed]
        result.avg_profit_factor = (
            round(statistics.mean(profit_factors), 4) if profit_factors else 0.0
        )

        # ── Extreme windows ────────────────────────────────────────────────────
        best_seg  = max(completed, key=lambda s: s.oos_metrics.total_pnl)
        worst_seg = min(completed, key=lambda s: s.oos_metrics.total_pnl)
        result.best_segment  = best_seg.window.segment_number
        result.worst_segment = worst_seg.window.segment_number

        # ── Walk-forward win rate ──────────────────────────────────────────────
        positive_segs = sum(1 for pnl in result.segment_pnls if pnl > 0)
        result.walk_forward_win_rate = round(positive_segs / len(completed), 4)

        logger.info(
            "WalkForwardAggregator: %d completed segments | "
            "total_pnl=%.0f | wf_win_rate=%.1f%% | avg_sharpe=%.4f",
            result.completed_segments,
            result.total_pnl,
            result.walk_forward_win_rate * 100,
            result.avg_sharpe,
        )
        return result
