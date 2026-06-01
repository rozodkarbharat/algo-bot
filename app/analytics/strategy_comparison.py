"""
Strategy comparison engine.

Three comparison modes:

1. strategy_vs_strategy
   Given N strategy IDs + one date range, produce a ranked table showing
   each strategy's core metrics side by side with rank annotations.

2. period_vs_period
   Given one strategy ID + two non-overlapping date ranges, show how the
   strategy's metrics evolved from period A to period B.

3. paper_vs_live
   Given one strategy ID + one date range, compute the same metrics against
   paper trades and live positions separately, then diff them.

Delegates all data collection and metric computation to
StrategyAttributionEngine to avoid duplication.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from app.analytics.strategy_attribution import StrategyAttributionEngine
from app.repositories.backtest_run_repository import BacktestRunRepository
from app.repositories.backtest_trade_repository import BacktestTradeRepository
from app.repositories.live_position_repository import LivePositionRepository
from app.repositories.paper_trade_repository import PaperTradeRepository
from app.schemas.performance import (
    PaperVsLiveResult,
    PeriodComparisonResult,
    PeriodLabel,
    PeriodSlice,
    StrategyComparisonResult,
    StrategyComparisonRow,
    TradingMode,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _rank(values: list[float], ascending: bool = False) -> list[int]:
    """Return 1-based rank for each value (1 = best)."""
    indexed = sorted(enumerate(values), key=lambda x: x[1], reverse=not ascending)
    ranks = [0] * len(values)
    for rank, (idx, _) in enumerate(indexed, 1):
        ranks[idx] = rank
    return ranks


class StrategyComparisonEngine:
    """
    Strategy comparison engine.

    Inject repositories for tests.
    """

    def __init__(
        self,
        paper_repo: Optional[PaperTradeRepository] = None,
        backtest_run_repo: Optional[BacktestRunRepository] = None,
        backtest_trade_repo: Optional[BacktestTradeRepository] = None,
        live_repo: Optional[LivePositionRepository] = None,
    ) -> None:
        kwargs = dict(
            paper_repo=paper_repo or PaperTradeRepository(),
            backtest_run_repo=backtest_run_repo or BacktestRunRepository(),
            backtest_trade_repo=backtest_trade_repo or BacktestTradeRepository(),
            live_repo=live_repo or LivePositionRepository(),
        )
        self._attr = StrategyAttributionEngine(**kwargs)

    # ── 1. Strategy vs Strategy ───────────────────────────────────────────────

    async def compare_strategies(
        self,
        strategy_ids: list[str],
        from_date: date,
        to_date: date,
        mode: TradingMode = TradingMode.PAPER,
    ) -> StrategyComparisonResult:
        """
        Compare multiple strategies over the same date range.

        Returns a ranked side-by-side table.
        """
        period = PeriodLabel.build(from_date, to_date)
        all_perfs = await self._attr.compute(from_date, to_date, mode)

        # Filter to requested strategy IDs; keep all if list is empty
        if strategy_ids:
            all_perfs = [p for p in all_perfs if p.strategy_id in strategy_ids]

        if not all_perfs:
            return StrategyComparisonResult(mode=mode, period=period, strategies=[])

        # Rank on each dimension
        pnls = [p.net_pnl for p in all_perfs]
        sharpes = [p.sharpe_ratio for p in all_perfs]
        win_rates = [p.win_rate for p in all_perfs]
        expectancies = [p.expectancy for p in all_perfs]
        drawdowns = [p.max_drawdown for p in all_perfs]

        pnl_ranks = _rank(pnls, ascending=False)
        sharpe_ranks = _rank(sharpes, ascending=False)
        wr_ranks = _rank(win_rates, ascending=False)
        exp_ranks = _rank(expectancies, ascending=False)
        dd_ranks = _rank(drawdowns, ascending=True)   # lower drawdown = better

        rows = []
        for i, p in enumerate(all_perfs):
            rows.append(
                StrategyComparisonRow(
                    strategy_id=p.strategy_id,
                    strategy_name=p.strategy_name,
                    total_trades=p.total_trades,
                    win_rate=p.win_rate,
                    net_pnl=p.net_pnl,
                    expectancy=p.expectancy,
                    sharpe_ratio=p.sharpe_ratio,
                    max_drawdown=p.max_drawdown,
                    profit_factor=p.profit_factor,
                    volatility=p.volatility,
                    ranks={
                        "net_pnl": pnl_ranks[i],
                        "sharpe_ratio": sharpe_ranks[i],
                        "win_rate": wr_ranks[i],
                        "expectancy": exp_ranks[i],
                        "max_drawdown": dd_ranks[i],
                    },
                )
            )

        rows.sort(key=lambda r: r.ranks.get("net_pnl", 999))

        best_pnl = max(all_perfs, key=lambda p: p.net_pnl).strategy_id
        best_sharpe = max(all_perfs, key=lambda p: p.sharpe_ratio).strategy_id
        best_wr = max(all_perfs, key=lambda p: p.win_rate).strategy_id
        best_exp = max(all_perfs, key=lambda p: p.expectancy).strategy_id
        lowest_dd = min(all_perfs, key=lambda p: p.max_drawdown).strategy_id

        logger.info(
            "[strategy-cmp] strategies=%d mode=%s best_pnl=%s",
            len(rows), mode.value, best_pnl,
        )
        return StrategyComparisonResult(
            mode=mode,
            period=period,
            strategies=rows,
            best_by_pnl=best_pnl,
            best_by_sharpe=best_sharpe,
            best_by_win_rate=best_wr,
            best_by_expectancy=best_exp,
            lowest_drawdown=lowest_dd,
        )

    # ── 2. Period vs Period ───────────────────────────────────────────────────

    async def compare_periods(
        self,
        strategy_id: str,
        period_a_from: date,
        period_a_to: date,
        period_b_from: date,
        period_b_to: date,
        mode: TradingMode = TradingMode.PAPER,
    ) -> PeriodComparisonResult:
        """
        Compare one strategy across two different date ranges.
        """
        perfs_a = await self._attr.compute(period_a_from, period_a_to, mode, strategy_id)
        perfs_b = await self._attr.compute(period_b_from, period_b_to, mode, strategy_id)

        a = perfs_a[0] if perfs_a else None
        b = perfs_b[0] if perfs_b else None

        def _slice(p, from_d, to_d):
            label = f"{from_d} to {to_d}"
            if p is None:
                return PeriodSlice(
                    from_date=from_d, to_date=to_d, label=label,
                    total_trades=0, win_rate=0.0, net_pnl=0.0,
                    expectancy=0.0, sharpe_ratio=0.0, max_drawdown=0.0,
                )
            return PeriodSlice(
                from_date=from_d,
                to_date=to_d,
                label=label,
                total_trades=p.total_trades,
                win_rate=p.win_rate,
                net_pnl=p.net_pnl,
                expectancy=p.expectancy,
                sharpe_ratio=p.sharpe_ratio,
                max_drawdown=p.max_drawdown,
            )

        slice_a = _slice(a, period_a_from, period_a_to)
        slice_b = _slice(b, period_b_from, period_b_to)

        return PeriodComparisonResult(
            strategy_id=strategy_id,
            strategy_name=(a or b).strategy_name if (a or b) else strategy_id,
            mode=mode,
            period_a=slice_a,
            period_b=slice_b,
            delta_pnl=round(slice_b.net_pnl - slice_a.net_pnl, 4),
            delta_win_rate=round(slice_b.win_rate - slice_a.win_rate, 6),
            delta_expectancy=round(slice_b.expectancy - slice_a.expectancy, 4),
            delta_sharpe=round(slice_b.sharpe_ratio - slice_a.sharpe_ratio, 4),
            improving=slice_b.net_pnl > slice_a.net_pnl,
        )

    # ── 3. Paper vs Live ──────────────────────────────────────────────────────

    async def compare_paper_vs_live(
        self,
        strategy_id: str,
        from_date: date,
        to_date: date,
    ) -> PaperVsLiveResult:
        """
        Compare paper-trading metrics vs live-trading metrics for a strategy.

        The delta (live − paper) on P&L shows the slippage impact of real execution.
        """
        period = PeriodLabel.build(from_date, to_date)

        paper_perfs = await self._attr.compute(from_date, to_date, TradingMode.PAPER, strategy_id)
        live_perfs = await self._attr.compute(from_date, to_date, TradingMode.LIVE, strategy_id)

        paper = paper_perfs[0] if paper_perfs else None
        live = live_perfs[0] if live_perfs else None

        slippage_impact = None
        wr_delta = None
        exp_delta = None
        if paper and live:
            slippage_impact = round(live.net_pnl - paper.net_pnl, 4)
            wr_delta = round(live.win_rate - paper.win_rate, 6)
            exp_delta = round(live.expectancy - paper.expectancy, 4)

        return PaperVsLiveResult(
            strategy_id=strategy_id,
            strategy_name=(paper or live).strategy_name if (paper or live) else strategy_id,
            period=period,
            paper=paper,
            live=live,
            slippage_impact=slippage_impact,
            win_rate_delta=wr_delta,
            expectancy_delta=exp_delta,
        )
