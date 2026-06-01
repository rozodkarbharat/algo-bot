"""
Reality Gap Analyzer — compares performance across Backtest, Paper Trading, and Live Trading.

The "reality gap" quantifies the difference between idealized backtest results and
real execution results. This module provides:

  - ModeMetrics: frozen dataclass capturing performance for one trading mode.
  - RealityGapResult: frozen dataclass capturing all pairwise gaps between modes.
  - RealityGapAnalyzer: async class that fetches data from MongoDB and computes gaps.

Gap interpretation (negative values mean real execution under-performs backtest):
  - win_rate_gap   < 0  → live/paper wins less often than backtest
  - pnl_gap        < 0  → live/paper earns less per trade than backtest
  - drawdown_gap   > 0  → live/paper suffers deeper drawdowns than backtest
  - expectancy_gap < 0  → live/paper expected value per trade is lower
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import mean
from typing import Optional

from app.models.backtest_metrics import BacktestMetrics
from app.models.backtest_run import BacktestRun, BacktestRunStatus
from app.models.backtest_trade import BacktestTrade
from app.models.live_position import LivePosition, LivePositionStatus
from app.models.live_signal import LiveSignal
from app.models.paper_trade import PaperTrade
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Minimum number of trades required on each side before a gap is meaningful.
_MIN_TRADES = 3


# ── Result types ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ModeMetrics:
    """Performance metrics for a single trading mode."""

    mode: str                        # "BACKTEST", "PAPER", or "LIVE"
    win_rate: float                  # 0.0 – 1.0
    avg_pnl_per_trade: float         # mean net P&L per trade (₹)
    total_pnl: float                 # sum of all trade P&L (₹)
    max_drawdown: float              # positive fraction, e.g. 0.05 = 5 %
    expectancy: float                # expected P&L per trade (₹)
    trade_count: int
    sharpe_ratio: Optional[float]    # annualised Sharpe, None if unavailable


@dataclass(frozen=True)
class RealityGapResult:
    """
    Pairwise reality gaps between backtest, paper trading, and live trading.

    Gap = actual_mode.metric - reference_mode.metric.
    A gap of None means insufficient data (< 3 trades on one side).
    """

    backtest: Optional[ModeMetrics]
    paper: Optional[ModeMetrics]
    live: Optional[ModeMetrics]

    # ── Paper vs Backtest gaps ────────────────────────────────────────────────
    paper_win_rate_gap: Optional[float]    # paper.win_rate - backtest.win_rate
    paper_pnl_gap: Optional[float]        # paper.avg_pnl  - backtest.avg_pnl
    paper_drawdown_gap: Optional[float]   # paper.max_dd   - backtest.max_dd
    paper_expectancy_gap: Optional[float] # paper.expectancy - backtest.expectancy

    # ── Live vs Backtest gaps ─────────────────────────────────────────────────
    live_win_rate_gap: Optional[float]
    live_pnl_gap: Optional[float]
    live_drawdown_gap: Optional[float]
    live_expectancy_gap: Optional[float]

    # ── Live vs Paper gaps ────────────────────────────────────────────────────
    live_vs_paper_win_rate_gap: Optional[float]
    live_vs_paper_pnl_gap: Optional[float]

    strategy_id: str
    analysis_period_days: int


# ── Analyzer ──────────────────────────────────────────────────────────────────


class RealityGapAnalyzer:
    """
    Computes the reality gap for a given strategy over a date range.

    Usage::

        analyzer = RealityGapAnalyzer()
        result = await analyzer.compute(
            strategy_id="one_side_orb",
            from_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            to_date=datetime(2024, 6, 30, tzinfo=timezone.utc),
        )
    """

    # ── Public API ────────────────────────────────────────────────────────────

    async def compute(
        self,
        strategy_id: str,
        from_date: datetime,
        to_date: datetime,
    ) -> RealityGapResult:
        """
        Fetch metrics for all three modes and compute pairwise reality gaps.

        Args:
            strategy_id: Strategy identifier to filter on.
            from_date:   Start of analysis window (UTC, inclusive).
            to_date:     End of analysis window (UTC, inclusive).

        Returns:
            RealityGapResult with all available gaps populated.
        """
        logger.info(
            "RealityGapAnalyzer.compute | strategy=%s from=%s to=%s",
            strategy_id,
            from_date.isoformat(),
            to_date.isoformat(),
        )

        backtest_metrics = await self._fetch_backtest_metrics(
            strategy_id, from_date, to_date
        )
        paper_metrics = await self._fetch_paper_metrics(
            strategy_id, from_date, to_date
        )
        live_metrics = await self._fetch_live_metrics(
            strategy_id, from_date, to_date
        )

        analysis_period_days = (to_date - from_date).days

        result = RealityGapResult(
            backtest=backtest_metrics,
            paper=paper_metrics,
            live=live_metrics,
            # paper vs backtest
            paper_win_rate_gap=self._gap(
                paper_metrics, backtest_metrics, "win_rate"
            ),
            paper_pnl_gap=self._gap(
                paper_metrics, backtest_metrics, "avg_pnl_per_trade"
            ),
            paper_drawdown_gap=self._gap(
                paper_metrics, backtest_metrics, "max_drawdown"
            ),
            paper_expectancy_gap=self._gap(
                paper_metrics, backtest_metrics, "expectancy"
            ),
            # live vs backtest
            live_win_rate_gap=self._gap(
                live_metrics, backtest_metrics, "win_rate"
            ),
            live_pnl_gap=self._gap(
                live_metrics, backtest_metrics, "avg_pnl_per_trade"
            ),
            live_drawdown_gap=self._gap(
                live_metrics, backtest_metrics, "max_drawdown"
            ),
            live_expectancy_gap=self._gap(
                live_metrics, backtest_metrics, "expectancy"
            ),
            # live vs paper
            live_vs_paper_win_rate_gap=self._gap(
                live_metrics, paper_metrics, "win_rate"
            ),
            live_vs_paper_pnl_gap=self._gap(
                live_metrics, paper_metrics, "avg_pnl_per_trade"
            ),
            strategy_id=strategy_id,
            analysis_period_days=analysis_period_days,
        )

        logger.info(
            "RealityGapAnalyzer.compute complete | strategy=%s "
            "bt_trades=%s paper_trades=%s live_trades=%s "
            "paper_pnl_gap=%s live_pnl_gap=%s",
            strategy_id,
            backtest_metrics.trade_count if backtest_metrics else None,
            paper_metrics.trade_count if paper_metrics else None,
            live_metrics.trade_count if live_metrics else None,
            result.paper_pnl_gap,
            result.live_pnl_gap,
        )
        return result

    # ── Private fetch helpers ─────────────────────────────────────────────────

    async def _fetch_backtest_metrics(
        self,
        strategy_id: str,
        from_date: datetime,
        to_date: datetime,
    ) -> Optional[ModeMetrics]:
        """
        Retrieve the latest completed BacktestRun for the strategy in the
        date range, load its BacktestMetrics document, and supplement
        avg_pnl_per_trade from the raw BacktestTrade records.
        """
        # Find the most recent completed run in the window.
        run: Optional[BacktestRun] = (
            await BacktestRun.find(
                BacktestRun.strategy_id == strategy_id,
                BacktestRun.status == BacktestRunStatus.COMPLETED,
                BacktestRun.created_at >= from_date,
                BacktestRun.created_at <= to_date,
            )
            .sort(-BacktestRun.created_at)
            .first_or_none()
        )

        if run is None:
            logger.debug(
                "_fetch_backtest_metrics | no completed run found for strategy=%s",
                strategy_id,
            )
            return None

        metrics: Optional[BacktestMetrics] = await BacktestMetrics.find_one(
            BacktestMetrics.run_id == run.run_id
        )

        if metrics is None:
            logger.warning(
                "_fetch_backtest_metrics | BacktestMetrics missing for run_id=%s",
                run.run_id,
            )
            return None

        if metrics.total_trades < _MIN_TRADES:
            logger.debug(
                "_fetch_backtest_metrics | too few trades (%d) for run_id=%s",
                metrics.total_trades,
                run.run_id,
            )
            return None

        # Derive avg_pnl_per_trade from raw trade records for accuracy.
        trades = await BacktestTrade.find(
            BacktestTrade.run_id == run.run_id
        ).to_list()

        executed = [t for t in trades if t.pnl != 0.0 or t.entry_price is not None]
        pnl_list = [t.pnl for t in executed] if executed else []
        avg_pnl = mean(pnl_list) if pnl_list else metrics.avg_pnl_per_trade

        return ModeMetrics(
            mode="BACKTEST",
            win_rate=metrics.win_rate,
            avg_pnl_per_trade=avg_pnl,
            total_pnl=metrics.total_pnl,
            max_drawdown=metrics.max_drawdown_percent,
            expectancy=metrics.expectancy,
            trade_count=metrics.total_trades,
            sharpe_ratio=metrics.sharpe_ratio,
        )

    async def _fetch_paper_metrics(
        self,
        strategy_id: str,
        from_date: datetime,
        to_date: datetime,
    ) -> Optional[ModeMetrics]:
        """
        Compute paper trading metrics from PaperTrade records in the window.
        """
        trades: list[PaperTrade] = await PaperTrade.find(
            PaperTrade.strategy_id == strategy_id,
            PaperTrade.trading_date >= from_date,
            PaperTrade.trading_date <= to_date,
        ).to_list()

        if len(trades) < _MIN_TRADES:
            logger.debug(
                "_fetch_paper_metrics | too few paper trades (%d) for strategy=%s",
                len(trades),
                strategy_id,
            )
            return None

        pnl_list = [t.pnl for t in trades]
        win_count = sum(1 for p in pnl_list if p > 0)
        win_rate = win_count / len(pnl_list)
        total_pnl = sum(pnl_list)
        avg_pnl = mean(pnl_list)
        expectancy = total_pnl / len(pnl_list)
        max_drawdown = self._compute_max_drawdown(pnl_list)

        return ModeMetrics(
            mode="PAPER",
            win_rate=win_rate,
            avg_pnl_per_trade=avg_pnl,
            total_pnl=total_pnl,
            max_drawdown=max_drawdown,
            expectancy=expectancy,
            trade_count=len(pnl_list),
            sharpe_ratio=None,
        )

    async def _fetch_live_metrics(
        self,
        strategy_id: str,
        from_date: datetime,
        to_date: datetime,
    ) -> Optional[ModeMetrics]:
        """
        Compute live trading metrics from closed LivePosition records in the window.

        LivePosition has no strategy_id field; filter via signal_ids that belong
        to the requested strategy.
        """
        # Resolve strategy-specific signal_ids for the period
        signals = await LiveSignal.find(
            LiveSignal.strategy_id == strategy_id,
            LiveSignal.trading_date >= from_date,
            LiveSignal.trading_date <= to_date,
        ).to_list()
        strategy_signal_ids = {s.signal_id for s in signals}

        positions: list[LivePosition] = await LivePosition.find(
            LivePosition.trading_date >= from_date,
            LivePosition.trading_date <= to_date,
            LivePosition.status == LivePositionStatus.CLOSED,
        ).to_list()

        # Filter to only positions linked to this strategy's signals
        if strategy_signal_ids:
            positions = [
                p for p in positions
                if p.signal_id and p.signal_id in strategy_signal_ids
            ]

        if len(positions) < _MIN_TRADES:
            logger.debug(
                "_fetch_live_metrics | too few live positions (%d) for strategy=%s",
                len(positions),
                strategy_id,
            )
            return None

        pnl_list = [p.realized_pnl for p in positions]
        win_count = sum(1 for p in pnl_list if p > 0)
        win_rate = win_count / len(pnl_list)
        total_pnl = sum(pnl_list)
        avg_pnl = mean(pnl_list)
        expectancy = total_pnl / len(pnl_list)
        max_drawdown = self._compute_max_drawdown(pnl_list)

        return ModeMetrics(
            mode="LIVE",
            win_rate=win_rate,
            avg_pnl_per_trade=avg_pnl,
            total_pnl=total_pnl,
            max_drawdown=max_drawdown,
            expectancy=expectancy,
            trade_count=len(pnl_list),
            sharpe_ratio=None,
        )

    # ── Gap computation ───────────────────────────────────────────────────────

    @staticmethod
    def _gap(
        actual: Optional[ModeMetrics],
        reference: Optional[ModeMetrics],
        field: str,
    ) -> Optional[float]:
        """
        Return (actual.field - reference.field), or None if either side has
        insufficient data (None or trade_count < _MIN_TRADES).
        """
        if actual is None or reference is None:
            return None
        if actual.trade_count < _MIN_TRADES or reference.trade_count < _MIN_TRADES:
            return None
        return getattr(actual, field) - getattr(reference, field)

    # ── Static helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _compute_max_drawdown(pnl_list: list[float]) -> float:
        """
        Compute maximum peak-to-trough drawdown as a positive fraction of peak.

        Args:
            pnl_list: Ordered list of per-trade net P&L values (₹).

        Returns:
            Maximum drawdown as a fraction in [0, 1].
            Returns 0.0 when the cumulative equity never exceeds zero.
        """
        peak: float = 0.0
        max_dd: float = 0.0
        cumulative: float = 0.0

        for pnl in pnl_list:
            cumulative += pnl
            peak = max(peak, cumulative)
            dd = (peak - cumulative) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd)

        return max_dd
