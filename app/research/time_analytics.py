"""
Time-of-day analytics engine.

Pure Python — NO database calls, NO I/O.
Analyses the relationship between entry time and trade profitability.

Time buckets (IST → UTC):
  09:30–10:00  (UTC 04:00–04:29)  early breakouts, strong institutional flow
  10:00–10:30  (UTC 04:30–04:59)  primary entry window
  10:30–11:00  (UTC 05:00–05:29)  mid-morning continuation
  11:00–11:30  (UTC 05:30–05:59)  late window, diminishing edge

Analysis surfaces:
  - Win rate and average P&L per bucket
  - SL hit rate per bucket (reveals where stops get run most)
  - Candidate days (how many setups attempted) vs executed per bucket
  - Best and worst performing bucket for LONG vs SHORT separately
  - Trend: does win rate decay as the session progresses?
"""

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import pytz

from app.models.backtest_trade import ExitReason, TradeSide
from app.utils.logger import get_logger

logger = get_logger(__name__)

IST = pytz.timezone("Asia/Kolkata")

# (label, start_utc_minutes, end_utc_minutes)
_TIME_BUCKETS: list[tuple[str, int, int]] = [
    ("09:30–10:00", 4 * 60,       4 * 60 + 29),
    ("10:00–10:30", 4 * 60 + 30,  4 * 60 + 59),
    ("10:30–11:00", 5 * 60,       5 * 60 + 29),
    ("11:00–11:30", 5 * 60 + 30,  5 * 60 + 59),
    ("11:30–12:00", 6 * 60,       6 * 60 + 29),
]


@dataclass
class TimeBucketStats:
    """Performance statistics for a single IST time bucket."""

    label: str                    # e.g. "09:30–10:00"
    total_entries: int = 0        # entries made in this bucket
    winning_trades: int = 0
    losing_trades: int = 0
    sl_hits: int = 0

    win_rate: float = 0.0
    sl_hit_rate: float = 0.0
    avg_pnl: float = 0.0
    total_pnl: float = 0.0
    avg_risk_reward: Optional[float] = None

    # Direction breakdown
    long_entries: int = 0
    short_entries: int = 0
    long_win_rate: float = 0.0
    short_win_rate: float = 0.0


@dataclass
class TimeAnalyticsResult:
    """
    Output of TimeAnalyticsEngine.analyse().

    Exposes bucket stats as a chronologically ordered list, plus
    directional comparisons and trend indicators.
    """

    buckets: list[TimeBucketStats] = field(default_factory=list)

    best_bucket: Optional[str] = None       # label of highest win-rate bucket
    worst_bucket: Optional[str] = None      # label of lowest win-rate bucket

    # Trend indicators (positive = win rate improves through the day)
    win_rate_trend: str = "flat"            # "improving" | "declining" | "flat"

    # Directional time edge
    best_long_bucket: Optional[str] = None
    best_short_bucket: Optional[str] = None

    metadata: dict = field(default_factory=dict)


class TimeAnalyticsEngine:
    """
    Analyses strategy performance across IST entry time buckets.

    Usage:
        engine = TimeAnalyticsEngine()
        result = engine.analyse(executed_trades)
    """

    def analyse(self, trades: list) -> TimeAnalyticsResult:
        """
        Compute time-slot analytics from a list of executed trades.

        Args:
            trades: BacktestTrade documents (or similar) with entry_time, pnl,
                    exit_reason, trade_side fields. NO_BREAKOUT trades are
                    ignored — they have no entry_time.

        Returns:
            TimeAnalyticsResult with per-bucket statistics.
        """
        executed = [t for t in trades if t.exit_reason != ExitReason.NO_BREAKOUT
                    and t.entry_time is not None]

        result = TimeAnalyticsResult()

        if not executed:
            logger.warning("TimeAnalyticsEngine: no executed trades to analyse.")
            return result

        # Bucket trades by entry time
        bucket_map: dict[str, list] = {b[0]: [] for b in _TIME_BUCKETS}
        unclassified = 0
        for trade in executed:
            utc_min = trade.entry_time.hour * 60 + trade.entry_time.minute
            placed = False
            for label, start, end in _TIME_BUCKETS:
                if start <= utc_min <= end:
                    bucket_map[label].append(trade)
                    placed = True
                    break
            if not placed:
                unclassified += 1

        # Build stats per bucket
        bucket_stats: list[TimeBucketStats] = []
        for label, _, _ in _TIME_BUCKETS:
            stats = self._compute_bucket_stats(label, bucket_map[label])
            bucket_stats.append(stats)

        result.buckets = bucket_stats

        # Identify best/worst (ignoring empty buckets)
        populated = [s for s in bucket_stats if s.total_entries >= 3]
        if populated:
            result.best_bucket  = max(populated, key=lambda s: s.win_rate).label
            result.worst_bucket = min(populated, key=lambda s: s.win_rate).label

            # Trend: compare first half vs second half of the day
            mid = len(populated) // 2
            first_half_wr = statistics.mean(s.win_rate for s in populated[:mid]) if mid > 0 else 0
            second_half_wr = statistics.mean(s.win_rate for s in populated[mid:]) if mid < len(populated) else 0
            diff = second_half_wr - first_half_wr
            if abs(diff) < 0.03:
                result.win_rate_trend = "flat"
            elif diff > 0:
                result.win_rate_trend = "improving"
            else:
                result.win_rate_trend = "declining"

            # Best bucket per direction
            long_pop  = [s for s in populated if s.long_entries >= 3]
            short_pop = [s for s in populated if s.short_entries >= 3]
            if long_pop:
                result.best_long_bucket  = max(long_pop,  key=lambda s: s.long_win_rate).label
            if short_pop:
                result.best_short_bucket = max(short_pop, key=lambda s: s.short_win_rate).label

        result.metadata = {
            "total_executed": len(executed),
            "unclassified_entries": unclassified,
        }

        logger.info(
            "TimeAnalyticsEngine: %d trades → best bucket=%s, trend=%s",
            len(executed),
            result.best_bucket,
            result.win_rate_trend,
        )
        return result

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _compute_bucket_stats(label: str, trades: list) -> TimeBucketStats:
        """Compute all stats for a single time bucket."""
        stats = TimeBucketStats(label=label)

        if not trades:
            return stats

        stats.total_entries = len(trades)
        winners  = [t for t in trades if t.pnl > 0]
        sl_hits  = [t for t in trades if t.exit_reason == ExitReason.SL_HIT]
        longs    = [t for t in trades if t.trade_side == TradeSide.LONG]
        shorts   = [t for t in trades if t.trade_side == TradeSide.SHORT]

        stats.winning_trades = len(winners)
        stats.losing_trades  = len(trades) - len(winners)
        stats.sl_hits = len(sl_hits)

        stats.win_rate    = round(len(winners) / len(trades), 4)
        stats.sl_hit_rate = round(len(sl_hits) / len(trades), 4)

        pnl_list = [t.pnl for t in trades]
        stats.avg_pnl   = round(statistics.mean(pnl_list), 2)
        stats.total_pnl = round(sum(pnl_list), 2)

        rr_values = [t.risk_reward for t in trades if t.risk_reward is not None]
        if rr_values:
            stats.avg_risk_reward = round(statistics.mean(rr_values), 4)

        stats.long_entries  = len(longs)
        stats.short_entries = len(shorts)

        if longs:
            long_wins = [t for t in longs if t.pnl > 0]
            stats.long_win_rate = round(len(long_wins) / len(longs), 4)
        if shorts:
            short_wins = [t for t in shorts if t.pnl > 0]
            stats.short_win_rate = round(len(short_wins) / len(shorts), 4)

        return stats
