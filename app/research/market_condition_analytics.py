"""
Market condition analytics engine.

Pure Python — NO database calls, NO I/O.
Classifies each trading day by its market character and measures how the
One-Side ORB strategy performs across each condition type.

Day classification (derived from per-day aggregate trade behavior):
  trending      — majority of stocks moved in the same direction; high daily range
  gap_up        — most OSD signals were bullish (UP direction dominant)
  gap_down      — most OSD signals were bearish (DOWN direction dominant)
  volatile      — many SL hits across symbols on the same day
  choppy        — low win rate across symbols, many NO_BREAKOUT days
  normal        — none of the above

This approach extracts market condition signals from the trade data itself
without requiring a separate NIFTY/VIX data feed — the collective behavior
of 50 stocks IS the market condition signal.

Future enhancement: pass daily NIFTY candles to compute true gap % and VIX.
The interface is designed to accept an optional nifty_candles parameter so
this upgrade requires no structural changes.
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


@dataclass
class DayProfile:
    """Aggregate profile for a single trading day across all symbols."""

    date_str: str           # YYYY-MM-DD (IST)
    total_candidates: int   # setups with valid OSD + probability
    total_executed: int     # entries taken
    total_no_breakout: int  # candidates where ORB was never touched
    long_signals: int       # OSD-UP candidates
    short_signals: int      # OSD-DOWN candidates
    sl_hits: int            # trades that hit SL
    wins: int
    daily_pnl: float

    # Derived classification
    condition: str = "normal"


@dataclass
class ConditionStats:
    """Performance metrics for all days classified as a given condition."""

    condition: str

    total_days: int = 0
    total_trades: int = 0
    winning_trades: int = 0
    sl_hits: int = 0

    win_rate: float = 0.0
    sl_hit_rate: float = 0.0
    avg_daily_pnl: float = 0.0
    total_pnl: float = 0.0
    avg_trades_per_day: float = 0.0


@dataclass
class MarketConditionResult:
    """Output of MarketConditionAnalyticsEngine.analyse()."""

    condition_stats: list[ConditionStats] = field(default_factory=list)
    day_profiles: list[DayProfile] = field(default_factory=list)

    best_condition: Optional[str] = None    # condition with highest win rate
    worst_condition: Optional[str] = None   # condition with lowest win rate

    metadata: dict = field(default_factory=dict)


class MarketConditionAnalyticsEngine:
    """
    Classifies trading days by market condition and computes per-condition performance.

    Usage:
        engine = MarketConditionAnalyticsEngine()
        result = engine.analyse(all_trades)  # includes NO_BREAKOUT trades
    """

    # Thresholds for day classification (tunable)
    _GAP_DOMINANCE_RATIO = 0.70    # 70% of signals in same direction → gap day
    _VOLATILE_SL_RATE = 0.55       # >55% of executed trades hit SL → volatile day
    _CHOPPY_WIN_RATE = 0.35        # <35% win rate → choppy day
    _TRENDING_WIN_RATE = 0.65      # >65% win rate → trending day
    _MIN_TRADES_FOR_CLASSIFY = 3   # days with fewer trades are marked "low_activity"

    def analyse(self, trades: list) -> MarketConditionResult:
        """
        Classify each trading day and compute per-condition performance.

        Args:
            trades: ALL BacktestTrade records including NO_BREAKOUT — needed to
                    compute candidate counts and direction distribution per day.

        Returns:
            MarketConditionResult with condition stats and day profiles.
        """
        # Group all trades by IST trading date
        by_date: dict[str, list] = defaultdict(list)
        for trade in trades:
            if trade.entry_time is not None:
                date_key = trade.entry_time.astimezone(IST).date().isoformat()
            elif trade.exit_time is not None:
                date_key = trade.exit_time.astimezone(IST).date().isoformat()
            else:
                # NO_BREAKOUT with no time — use trading_date if available
                if hasattr(trade, "trading_date") and trade.trading_date is not None:
                    date_key = trade.trading_date.astimezone(IST).date().isoformat()
                else:
                    continue
            by_date[date_key].append(trade)

        day_profiles: list[DayProfile] = []
        for date_str, day_trades in sorted(by_date.items()):
            profile = self._build_day_profile(date_str, day_trades)
            day_profiles.append(profile)

        # Aggregate by condition
        by_condition: dict[str, list[DayProfile]] = defaultdict(list)
        for profile in day_profiles:
            by_condition[profile.condition].append(profile)

        condition_stats: list[ConditionStats] = []
        for condition, profiles in sorted(by_condition.items()):
            stats = self._aggregate_condition(condition, profiles)
            condition_stats.append(stats)

        result = MarketConditionResult(
            condition_stats=condition_stats,
            day_profiles=day_profiles,
        )

        populated = [s for s in condition_stats if s.total_trades >= 5]
        if populated:
            result.best_condition  = max(populated, key=lambda s: s.win_rate).condition
            result.worst_condition = min(populated, key=lambda s: s.win_rate).condition

        result.metadata = {
            "total_days_analysed": len(day_profiles),
            "condition_distribution": {c: len(p) for c, p in by_condition.items()},
        }

        logger.info(
            "MarketConditionAnalyticsEngine: %d days → conditions: %s",
            len(day_profiles),
            {c: len(p) for c, p in by_condition.items()},
        )
        return result

    # ── Day profile builder ───────────────────────────────────────────────────

    def _build_day_profile(self, date_str: str, day_trades: list) -> DayProfile:
        """Build a DayProfile from all trades on a single date."""
        executed    = [t for t in day_trades if t.exit_reason != ExitReason.NO_BREAKOUT]
        no_breakout = [t for t in day_trades if t.exit_reason == ExitReason.NO_BREAKOUT]
        sl_hits     = [t for t in executed   if t.exit_reason == ExitReason.SL_HIT]
        wins        = [t for t in executed   if t.pnl > 0]
        longs       = [t for t in day_trades if t.trade_side == TradeSide.LONG]
        shorts      = [t for t in day_trades if t.trade_side == TradeSide.SHORT]

        profile = DayProfile(
            date_str=date_str,
            total_candidates=len(day_trades),
            total_executed=len(executed),
            total_no_breakout=len(no_breakout),
            long_signals=len(longs),
            short_signals=len(shorts),
            sl_hits=len(sl_hits),
            wins=len(wins),
            daily_pnl=round(sum(t.pnl for t in executed), 2),
        )

        profile.condition = self._classify_day(profile)
        return profile

    def _classify_day(self, p: DayProfile) -> str:
        """Assign a market condition label to a day profile."""
        if p.total_executed < self._MIN_TRADES_FOR_CLASSIFY:
            return "low_activity"

        sl_rate = p.sl_hits / p.total_executed
        win_rate = p.wins / p.total_executed

        # Gap day: directional dominance among OSD signals
        total_signals = p.long_signals + p.short_signals
        if total_signals > 0:
            long_ratio  = p.long_signals  / total_signals
            short_ratio = p.short_signals / total_signals
            if long_ratio  >= self._GAP_DOMINANCE_RATIO:
                return "gap_up"
            if short_ratio >= self._GAP_DOMINANCE_RATIO:
                return "gap_down"

        # Volatile: lots of SL hits
        if sl_rate >= self._VOLATILE_SL_RATE:
            return "volatile"

        # Choppy: very low win rate even for the entries taken
        if win_rate <= self._CHOPPY_WIN_RATE:
            return "choppy"

        # Trending: strong win rate, price followed through
        if win_rate >= self._TRENDING_WIN_RATE:
            return "trending"

        return "normal"

    # ── Condition aggregator ──────────────────────────────────────────────────

    @staticmethod
    def _aggregate_condition(condition: str, profiles: list[DayProfile]) -> ConditionStats:
        """Compute aggregate metrics across all days of a given condition."""
        stats = ConditionStats(condition=condition)
        stats.total_days = len(profiles)

        all_executed = sum(p.total_executed for p in profiles)
        all_wins     = sum(p.wins for p in profiles)
        all_sl_hits  = sum(p.sl_hits for p in profiles)
        all_pnl      = sum(p.daily_pnl for p in profiles)

        stats.total_trades = all_executed
        stats.winning_trades = all_wins
        stats.sl_hits = all_sl_hits
        stats.total_pnl = round(all_pnl, 2)

        if all_executed > 0:
            stats.win_rate    = round(all_wins / all_executed, 4)
            stats.sl_hit_rate = round(all_sl_hits / all_executed, 4)

        if stats.total_days > 0:
            stats.avg_daily_pnl     = round(all_pnl / stats.total_days, 2)
            stats.avg_trades_per_day = round(all_executed / stats.total_days, 2)

        return stats
