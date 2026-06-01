"""
Backtesting metrics calculation engine.

Pure Python — NO database calls, NO I/O.
Receives a list of SimulatedTrade results and computes all performance
statistics, returning a fully populated BacktestMetrics document.

Metrics computed:
  Core:        win_rate, sl_hit_rate, breakout_success_rate
  P&L:         total_pnl, avg_pnl, avg_win, avg_loss, max_win, max_loss
  Risk:        max_drawdown, max_drawdown_percent, profit_factor, expectancy
  Sharpe:      annualised Sharpe ratio from daily returns
  Consecutive: max_consecutive_wins, max_consecutive_losses
  Breakdowns:  per_symbol, daily_pnl, monthly_pnl

Design:
  - Uses only the standard library (math, statistics, collections) — no pandas.
    This keeps the engine deployable without heavy data-science dependencies.
  - All dollar amounts are in ₹ (Indian Rupees).
  - Daily Sharpe annualised with √252 (trading days per year).
"""

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import pytz

from app.models.backtest_trade import ExitReason, TradeSide
from app.strategy.trade_simulator import SimulatedTrade
from app.utils.logger import get_logger

logger = get_logger(__name__)

IST = pytz.timezone("Asia/Kolkata")
_TRADING_DAYS_PER_YEAR = 252


@dataclass
class MetricsResult:
    """
    Plain Python dataclass holding all calculated backtest metrics.

    Returned by MetricsEngine.calculate() — deliberately NOT a Beanie Document
    so the engine stays database-independent and fully unit-testable.

    BacktestService converts this to a BacktestMetrics document before persisting.
    """
    run_id: str

    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    no_entry_days: int = 0
    total_candidate_days: int = 0

    win_rate: float = 0.0
    sl_hit_rate: float = 0.0
    breakout_success_rate: float = 0.0

    total_pnl: float = 0.0
    avg_pnl_per_trade: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    max_win: float = 0.0
    max_loss: float = 0.0

    max_drawdown: float = 0.0
    max_drawdown_percent: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    sharpe_ratio: Optional[float] = None
    avg_risk_reward: Optional[float] = None

    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0

    per_symbol_metrics: dict = field(default_factory=dict)
    daily_pnl: dict = field(default_factory=dict)
    monthly_pnl: dict = field(default_factory=dict)


class MetricsEngine:
    """
    Computes all performance metrics from a list of SimulatedTrade records.

    Usage:
        engine = MetricsEngine()
        result = engine.calculate(
            run_id="...",
            trades=[...],
            total_candidate_days=200,
        )
        # result is a MetricsResult dataclass, not a Beanie document.
        # BacktestService converts it to BacktestMetrics before saving.
    """

    def calculate(
        self,
        run_id: str,
        trades: list[SimulatedTrade],
        total_candidate_days: int,
    ) -> MetricsResult:
        """
        Calculate all metrics from the trade list.

        Args:
            run_id:                 BacktestRun.run_id to associate with.
            trades:                 All SimulatedTrade results from the engine.
            total_candidate_days:   Total days where a setup was identified
                                    (= executed + no-breakout days).

        Returns:
            Fully populated MetricsResult (plain dataclass, no DB dependency).
        """
        metrics = MetricsResult(run_id=run_id)
        metrics.total_candidate_days = total_candidate_days

        # Separate executed trades (entry was taken) from no-breakout days
        executed = [t for t in trades if t.exit_reason != ExitReason.NO_BREAKOUT]
        no_entry = [t for t in trades if t.exit_reason == ExitReason.NO_BREAKOUT]

        metrics.no_entry_days = len(no_entry)
        metrics.total_trades = len(executed)

        if not executed:
            logger.warning(
                "MetricsEngine: run_id=%s — no executed trades to compute metrics from.",
                run_id,
            )
            metrics.breakout_success_rate = 0.0
            return metrics

        # ── Breakout success rate ─────────────────────────────────────────────
        if total_candidate_days > 0:
            metrics.breakout_success_rate = round(
                len(executed) / total_candidate_days, 4
            )

        # ── Classify wins / losses ────────────────────────────────────────────
        winning = [t for t in executed if t.pnl > 0]
        losing  = [t for t in executed if t.pnl <= 0]
        sl_hits = [t for t in executed if t.exit_reason == ExitReason.SL_HIT]

        metrics.winning_trades = len(winning)
        metrics.losing_trades  = len(losing)
        metrics.win_rate = round(len(winning) / len(executed), 4) if executed else 0.0
        metrics.sl_hit_rate = round(len(sl_hits) / len(executed), 4) if executed else 0.0

        # ── P&L aggregates ────────────────────────────────────────────────────
        all_pnl = [t.pnl for t in executed]
        metrics.total_pnl = round(sum(all_pnl), 2)
        metrics.avg_pnl_per_trade = round(statistics.mean(all_pnl), 2)

        if winning:
            winning_pnl = [t.pnl for t in winning]
            metrics.avg_win = round(statistics.mean(winning_pnl), 2)
            metrics.max_win = round(max(winning_pnl), 2)

        if losing:
            losing_pnl = [t.pnl for t in losing]
            metrics.avg_loss = round(statistics.mean(losing_pnl), 2)
            metrics.max_loss = round(min(losing_pnl), 2)

        # ── Risk metrics ──────────────────────────────────────────────────────
        gross_profit = sum(t.pnl for t in winning) if winning else 0.0
        gross_loss   = abs(sum(t.pnl for t in losing)) if losing else 0.0

        metrics.profit_factor = (
            round(gross_profit / gross_loss, 4) if gross_loss > 0 else 0.0
        )

        loss_rate = 1.0 - metrics.win_rate
        metrics.expectancy = round(
            (metrics.win_rate * metrics.avg_win)
            - (loss_rate * abs(metrics.avg_loss)),
            2,
        )

        # ── Max drawdown (from equity curve) ──────────────────────────────────
        max_dd, max_dd_pct = self._compute_max_drawdown(all_pnl)
        metrics.max_drawdown = round(max_dd, 2)
        metrics.max_drawdown_percent = round(max_dd_pct, 4)

        # ── Sharpe ratio ──────────────────────────────────────────────────────
        metrics.sharpe_ratio = self._compute_sharpe(executed)

        # ── Avg risk-reward ───────────────────────────────────────────────────
        rr_values = [t.risk_reward for t in executed if t.risk_reward is not None]
        if rr_values:
            metrics.avg_risk_reward = round(statistics.mean(rr_values), 4)

        # ── Consecutive wins / losses ─────────────────────────────────────────
        metrics.max_consecutive_wins, metrics.max_consecutive_losses = (
            self._compute_consecutive(executed)
        )

        # ── Per-symbol breakdown ──────────────────────────────────────────────
        metrics.per_symbol_metrics = self._compute_per_symbol(executed)

        # ── Daily and monthly P&L ─────────────────────────────────────────────
        metrics.daily_pnl, metrics.monthly_pnl = self._compute_time_breakdowns(executed)

        return metrics

    # ── Private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _compute_max_drawdown(
        pnl_series: list[float],
    ) -> tuple[float, float]:
        """
        Compute max drawdown (absolute ₹ and % of peak) from a sequential P&L series.

        Returns (max_drawdown_₹, max_drawdown_percent).
        """
        if not pnl_series:
            return 0.0, 0.0

        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        max_dd_pct = 0.0

        for pnl in pnl_series:
            equity += pnl
            if equity > peak:
                peak = equity
            drawdown = peak - equity
            if drawdown > max_dd:
                max_dd = drawdown
                max_dd_pct = (drawdown / peak * 100.0) if peak > 0 else 0.0

        return max_dd, max_dd_pct

    @staticmethod
    def _compute_sharpe(executed: list[SimulatedTrade]) -> Optional[float]:
        """
        Compute the annualised Sharpe ratio from daily returns.

        Groups P&L by IST trading date, calculates daily return as
        pnl / capital_used for each trade (approximate per-trade capital basis).
        Returns None if fewer than 2 trading days.
        """
        if len(executed) < 2:
            return None

        # Group net P&L by IST date
        daily: dict[str, float] = defaultdict(float)
        daily_cap: dict[str, float] = defaultdict(float)

        for trade in executed:
            if trade.entry_time is None:
                continue
            ist_date = trade.entry_time.astimezone(IST).date().isoformat()
            daily[ist_date] += trade.pnl
            daily_cap[ist_date] = max(daily_cap[ist_date], trade.capital_used)

        if len(daily) < 2:
            return None

        # Daily return = daily_pnl / max_capital_used_that_day
        daily_returns = []
        for d, pnl in daily.items():
            cap = daily_cap.get(d, 0.0)
            if cap > 0:
                daily_returns.append(pnl / cap)

        if len(daily_returns) < 2:
            return None

        mean_ret = statistics.mean(daily_returns)
        std_ret  = statistics.stdev(daily_returns)

        if std_ret == 0.0:
            return None

        sharpe = (mean_ret / std_ret) * math.sqrt(_TRADING_DAYS_PER_YEAR)
        return round(sharpe, 4)

    @staticmethod
    def _compute_consecutive(
        executed: list[SimulatedTrade],
    ) -> tuple[int, int]:
        """Return (max_consecutive_wins, max_consecutive_losses)."""
        max_wins = cur_wins = 0
        max_losses = cur_losses = 0

        for trade in executed:
            if trade.pnl > 0:
                cur_wins += 1
                cur_losses = 0
                max_wins = max(max_wins, cur_wins)
            else:
                cur_losses += 1
                cur_wins = 0
                max_losses = max(max_losses, cur_losses)

        return max_wins, max_losses

    @staticmethod
    def _compute_per_symbol(
        executed: list[SimulatedTrade],
    ) -> dict:
        """
        Build per-symbol performance breakdown.

        Returns dict: symbol → {total, wins, losses, pnl, win_rate,
                                 avg_pnl, best_trade, worst_trade}
        """
        by_symbol: dict[str, list[SimulatedTrade]] = defaultdict(list)
        for trade in executed:
            by_symbol[trade.symbol].append(trade)

        result = {}
        for symbol, sym_trades in by_symbol.items():
            wins = [t for t in sym_trades if t.pnl > 0]
            losses = [t for t in sym_trades if t.pnl <= 0]
            total_pnl = sum(t.pnl for t in sym_trades)
            result[symbol] = {
                "total": len(sym_trades),
                "wins": len(wins),
                "losses": len(losses),
                "pnl": round(total_pnl, 2),
                "win_rate": round(len(wins) / len(sym_trades), 4),
                "avg_pnl": round(total_pnl / len(sym_trades), 2),
                "best_trade": round(max(t.pnl for t in sym_trades), 2),
                "worst_trade": round(min(t.pnl for t in sym_trades), 2),
            }
        return result

    @staticmethod
    def _compute_time_breakdowns(
        executed: list[SimulatedTrade],
    ) -> tuple[dict, dict]:
        """
        Compute daily and monthly P&L breakdowns.

        Returns (daily_pnl dict keyed "YYYY-MM-DD", monthly_pnl dict keyed "YYYY-MM").
        """
        daily: dict[str, float] = defaultdict(float)
        monthly: dict[str, float] = defaultdict(float)

        for trade in executed:
            if trade.entry_time is None:
                continue
            ist_dt = trade.entry_time.astimezone(IST)
            day_key   = ist_dt.date().isoformat()
            month_key = ist_dt.strftime("%Y-%m")
            daily[day_key]     += trade.pnl
            monthly[month_key] += trade.pnl

        daily_rounded   = {k: round(v, 2) for k, v in sorted(daily.items())}
        monthly_rounded = {k: round(v, 2) for k, v in sorted(monthly.items())}
        return daily_rounded, monthly_rounded
