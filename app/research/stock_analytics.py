"""
Stock performance analytics engine.

Pure Python — NO database calls, NO I/O.
Receives a list of SimulatedTrade (or BacktestTrade-like) records and
computes per-symbol rankings to answer: "which stocks have real edge?"

Metrics computed per symbol:
  - Win rate, SL hit rate, breakout success rate
  - Total and average P&L, max win/loss
  - Expectancy (expected value per trade)
  - Profit factor
  - Max drawdown across symbol's trades
  - Best entry time bucket (IST)
  - Average ORB range %, average post-breakout move %

Ranking dimensions:
  - Tradable rank: composite score of win_rate × expectancy (excludes low trade-count)
  - Avoidance rank: sl_hit_rate and negative expectancy (stocks to skip)
"""

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import pytz

from app.models.backtest_trade import ExitReason
from app.utils.logger import get_logger

logger = get_logger(__name__)

IST = pytz.timezone("Asia/Kolkata")

# Minimum trades for a symbol to be included in rankings
_MIN_TRADES_FOR_RANKING = 3

# IST time buckets for breakout time analysis per symbol
_TIME_BUCKETS: list[tuple[str, int, int]] = [
    ("09:30–10:00", 4 * 60,      4 * 60 + 29),   # 9:30–10:00 IST → UTC 4:00–4:29
    ("10:00–10:30", 4 * 60 + 30, 4 * 60 + 59),
    ("10:30–11:00", 5 * 60,      5 * 60 + 29),
    ("11:00–11:30", 5 * 60 + 30, 5 * 60 + 59),
    ("11:30–12:00", 6 * 60,      6 * 60 + 29),
]


@dataclass
class SymbolAnalytics:
    """Complete performance analytics for one NSE symbol."""

    symbol: str

    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    no_entry_days: int = 0          # candidate days where ORB was never broken

    win_rate: float = 0.0
    sl_hit_rate: float = 0.0
    breakout_success_rate: float = 0.0

    total_pnl: float = 0.0
    avg_pnl: float = 0.0
    max_win: float = 0.0
    max_loss: float = 0.0

    expectancy: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0

    avg_orb_range_pct: float = 0.0
    avg_move_after_breakout_pct: float = 0.0

    best_breakout_time_range: Optional[str] = None

    # Composite tradability score: win_rate × max(0, expectancy), clamped [0, 1]
    tradability_score: float = 0.0


@dataclass
class StockAnalyticsResult:
    """
    Aggregated output of StockAnalyticsEngine.analyse().

    Returned to ResearchService which converts it to StockPerformanceAnalytics
    Beanie documents and also includes it in the report.
    """

    symbol_analytics: list[SymbolAnalytics] = field(default_factory=list)

    # Pre-sorted views for fast access
    top_performers: list[SymbolAnalytics] = field(default_factory=list)
    worst_performers: list[SymbolAnalytics] = field(default_factory=list)
    high_sl_risk: list[SymbolAnalytics] = field(default_factory=list)   # sl_hit_rate > 0.5

    metadata: dict = field(default_factory=dict)


class StockAnalyticsEngine:
    """
    Computes per-symbol performance rankings from a trade list.

    Usage:
        engine = StockAnalyticsEngine()
        result = engine.analyse(trades)
        # result.top_performers is sorted by tradability_score descending
    """

    def analyse(self, trades: list) -> StockAnalyticsResult:
        """
        Compute analytics for all symbols present in the trade list.

        Args:
            trades: List of BacktestTrade documents (or SimulatedTrade objects).
                    Both have the same field names used here.

        Returns:
            StockAnalyticsResult with per-symbol breakdown and ranked lists.
        """
        by_symbol: dict[str, list] = defaultdict(list)
        for trade in trades:
            by_symbol[trade.symbol].append(trade)

        all_analytics: list[SymbolAnalytics] = []
        for symbol, sym_trades in by_symbol.items():
            analytics = self._analyse_symbol(symbol, sym_trades)
            all_analytics.append(analytics)

        result = StockAnalyticsResult(symbol_analytics=all_analytics)

        qualified = [a for a in all_analytics if a.total_trades >= _MIN_TRADES_FOR_RANKING]

        result.top_performers = sorted(
            qualified, key=lambda a: a.tradability_score, reverse=True
        )[:20]

        result.worst_performers = sorted(
            qualified, key=lambda a: a.total_pnl
        )[:20]

        result.high_sl_risk = sorted(
            [a for a in qualified if a.sl_hit_rate > 0.5],
            key=lambda a: a.sl_hit_rate,
            reverse=True,
        )

        result.metadata = {
            "total_symbols": len(all_analytics),
            "qualified_symbols": len(qualified),
            "min_trades_threshold": _MIN_TRADES_FOR_RANKING,
        }

        logger.info(
            "StockAnalyticsEngine: %d symbols analysed, %d qualified for ranking.",
            len(all_analytics),
            len(qualified),
        )
        return result

    # ── Per-symbol computation ─────────────────────────────────────────────────

    def _analyse_symbol(self, symbol: str, trades: list) -> SymbolAnalytics:
        """Compute full analytics for a single symbol."""
        analytics = SymbolAnalytics(symbol=symbol)

        executed = [t for t in trades if t.exit_reason != ExitReason.NO_BREAKOUT]
        no_entry = [t for t in trades if t.exit_reason == ExitReason.NO_BREAKOUT]

        analytics.no_entry_days = len(no_entry)
        analytics.total_trades = len(executed)

        total_candidates = len(trades)
        if total_candidates > 0:
            analytics.breakout_success_rate = round(len(executed) / total_candidates, 4)

        if not executed:
            return analytics

        # Classify outcomes
        winners = [t for t in executed if t.pnl > 0]
        losers  = [t for t in executed if t.pnl <= 0]
        sl_hits = [t for t in executed if t.exit_reason == ExitReason.SL_HIT]

        analytics.winning_trades = len(winners)
        analytics.losing_trades  = len(losers)
        analytics.win_rate    = round(len(winners) / len(executed), 4)
        analytics.sl_hit_rate = round(len(sl_hits) / len(executed), 4)

        # P&L aggregates
        pnl_list = [t.pnl for t in executed]
        analytics.total_pnl = round(sum(pnl_list), 2)
        analytics.avg_pnl   = round(statistics.mean(pnl_list), 2)
        analytics.max_win   = round(max(pnl_list), 2)
        analytics.max_loss  = round(min(pnl_list), 2)

        # Expectancy
        avg_win  = statistics.mean([t.pnl for t in winners]) if winners else 0.0
        avg_loss = statistics.mean([t.pnl for t in losers])  if losers  else 0.0
        loss_rate = 1.0 - analytics.win_rate
        analytics.expectancy = round(
            (analytics.win_rate * avg_win) - (loss_rate * abs(avg_loss)), 2
        )

        # Profit factor
        gross_profit = sum(t.pnl for t in winners) if winners else 0.0
        gross_loss   = abs(sum(t.pnl for t in losers)) if losers else 0.0
        analytics.profit_factor = (
            round(gross_profit / gross_loss, 4) if gross_loss > 0 else 0.0
        )

        # Max drawdown (sequential across symbol's trades, chronological order)
        analytics.max_drawdown = self._compute_max_drawdown(pnl_list)

        # ORB range and post-breakout move
        analytics.avg_orb_range_pct = self._avg_orb_range(executed)
        analytics.avg_move_after_breakout_pct = self._avg_post_breakout_move(executed)

        # Best entry time bucket
        analytics.best_breakout_time_range = self._best_time_bucket(executed)

        # Composite tradability score
        analytics.tradability_score = round(
            analytics.win_rate * max(0.0, analytics.expectancy / 1000.0), 6
        )

        return analytics

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _compute_max_drawdown(pnl_series: list[float]) -> float:
        """Return max drawdown (₹) from sequential P&L series."""
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for pnl in pnl_series:
            equity += pnl
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd
        return round(max_dd, 2)

    @staticmethod
    def _avg_orb_range(executed: list) -> float:
        """Average ORB range % across all executed trades."""
        ranges = []
        for t in executed:
            if t.orb_low > 0:
                ranges.append((t.orb_high - t.orb_low) / t.orb_low * 100)
        return round(statistics.mean(ranges), 4) if ranges else 0.0

    @staticmethod
    def _avg_post_breakout_move(executed: list) -> float:
        """
        Average % move from entry price to exit price (unsigned, in trade direction).

        Measures how much the stock actually moves after the breakout confirms.
        """
        moves = []
        for t in executed:
            if t.entry_price and t.exit_price and t.entry_price > 0:
                from app.models.backtest_trade import TradeSide
                if t.trade_side == TradeSide.LONG:
                    move = (t.exit_price - t.entry_price) / t.entry_price * 100
                else:
                    move = (t.entry_price - t.exit_price) / t.entry_price * 100
                moves.append(move)
        return round(statistics.mean(moves), 4) if moves else 0.0

    @staticmethod
    def _best_time_bucket(executed: list) -> Optional[str]:
        """
        Find the IST time bucket with the highest win rate for this symbol.

        Returns None if no trades have entry times or bucket sizes are too small.
        """
        bucket_trades: dict[str, list] = {b[0]: [] for b in _TIME_BUCKETS}
        for trade in executed:
            if trade.entry_time is None:
                continue
            t = trade.entry_time
            total_utc_min = t.hour * 60 + t.minute
            for label, start_utc, end_utc in _TIME_BUCKETS:
                if start_utc <= total_utc_min <= end_utc:
                    bucket_trades[label].append(trade)
                    break

        best_label: Optional[str] = None
        best_win_rate = -1.0
        for label, bucket in bucket_trades.items():
            if len(bucket) < 2:
                continue
            wr = len([t for t in bucket if t.pnl > 0]) / len(bucket)
            if wr > best_win_rate:
                best_win_rate = wr
                best_label = label

        return best_label
