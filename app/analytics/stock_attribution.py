"""
Stock attribution engine.

Answers "Which stocks are making money?" by aggregating per-symbol P&L
across all strategies in a date range.

Produces:
  - top performers (by net P&L)
  - worst performers (by net P&L)
  - contribution % to total portfolio P&L
  - consistency score: win_rate × log2(trade_count + 1)
  - per-strategy P&L breakdown per symbol

Data sources: same as strategy_attribution (paper / backtest / live).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Optional

from app.analytics.math_helpers import (
    avg_win_avg_loss,
    consistency_score,
    contribution_pct,
    expectancy,
    win_rate as compute_win_rate,
)
from app.repositories.backtest_run_repository import BacktestRunRepository
from app.repositories.backtest_trade_repository import BacktestTradeRepository
from app.repositories.live_position_repository import LivePositionRepository
from app.repositories.paper_trade_repository import PaperTradeRepository
from app.schemas.performance import (
    PeriodLabel,
    StockContributionBreakdown,
    StockPerformance,
    TradingMode,
)
from app.utils.logger import get_logger
from app.utils.market_time import date_to_utc_midnight

logger = get_logger(__name__)


class StockAttributionEngine:
    """
    Aggregates per-symbol performance and ranks stocks by contribution.

    Inject custom repositories for tests.
    """

    def __init__(
        self,
        paper_repo: Optional[PaperTradeRepository] = None,
        backtest_run_repo: Optional[BacktestRunRepository] = None,
        backtest_trade_repo: Optional[BacktestTradeRepository] = None,
        live_repo: Optional[LivePositionRepository] = None,
    ) -> None:
        self._paper = paper_repo or PaperTradeRepository()
        self._bt_run = backtest_run_repo or BacktestRunRepository()
        self._bt_trade = backtest_trade_repo or BacktestTradeRepository()
        self._live = live_repo or LivePositionRepository()

    # ── Public API ────────────────────────────────────────────────────────────

    async def compute(
        self,
        from_date: date,
        to_date: date,
        mode: TradingMode = TradingMode.PAPER,
        min_trades: int = 1,
        top_n: int = 10,
    ) -> list[StockPerformance]:
        """
        Return stock performance sorted by net_pnl (descending).

        Parameters
        ----------
        min_trades : int
            Exclude symbols with fewer trades than this threshold.
        top_n : int
            Maximum number of results to return.
        """
        period = PeriodLabel.build(from_date, to_date)
        from_dt = date_to_utc_midnight(from_date)
        to_dt = date_to_utc_midnight(to_date)

        # symbol → strategy_id → list[float pnl]
        raw: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        strat_names: dict[str, str] = {}

        if mode in (TradingMode.PAPER, TradingMode.COMBINED):
            await self._collect_paper(raw, strat_names, from_dt, to_dt)
        if mode in (TradingMode.BACKTEST, TradingMode.COMBINED):
            await self._collect_backtest(raw, strat_names, from_date, to_date)
        if mode in (TradingMode.LIVE, TradingMode.COMBINED):
            await self._collect_live(raw, strat_names, from_dt, to_dt)

        # Compute total portfolio P&L for contribution %
        all_pnls = [
            pnl
            for sym_data in raw.values()
            for pnls in sym_data.values()
            for pnl in pnls
        ]
        total_pnl = sum(all_pnls)

        results: list[StockPerformance] = []
        for symbol, strat_data in raw.items():
            sym_pnls = [p for pnls in strat_data.values() for p in pnls]
            if len(sym_pnls) < min_trades:
                continue

            wins = sum(1 for p in sym_pnls if p > 0)
            total = len(sym_pnls)
            wrate = compute_win_rate(wins, total)
            net_pnl = round(sum(sym_pnls), 4)
            avg_pnl = round(net_pnl / total, 4) if total else 0.0
            avg_w, avg_l = avg_win_avg_loss(sym_pnls)
            exp = expectancy(avg_w, avg_l, wrate)
            contrib = contribution_pct(net_pnl, total_pnl)
            cons = consistency_score(wrate, total)

            breakdowns = []
            for sid, pnls in strat_data.items():
                s_wins = sum(1 for p in pnls if p > 0)
                s_wrate = compute_win_rate(s_wins, len(pnls))
                breakdowns.append(
                    StockContributionBreakdown(
                        strategy_id=sid,
                        strategy_name=strat_names.get(sid, sid),
                        trades=len(pnls),
                        net_pnl=round(sum(pnls), 4),
                        win_rate=s_wrate,
                    )
                )
            breakdowns.sort(key=lambda b: b.net_pnl, reverse=True)

            results.append(
                StockPerformance(
                    symbol=symbol,
                    mode=mode,
                    period=period,
                    total_trades=total,
                    wins=wins,
                    losses=total - wins,
                    win_rate=wrate,
                    net_pnl=net_pnl,
                    avg_pnl=avg_pnl,
                    expectancy=exp,
                    contribution_pct=contrib,
                    consistency_score=cons,
                    strategy_breakdown=breakdowns,
                )
            )

        results.sort(key=lambda s: s.net_pnl, reverse=True)
        truncated = results[:top_n]
        logger.info(
            "[stock-attr] %s mode=%s symbols=%d total_pnl=%.2f",
            period.label, mode.value, len(results), total_pnl,
        )
        return truncated

    async def top_performers(
        self,
        from_date: date,
        to_date: date,
        mode: TradingMode = TradingMode.PAPER,
        n: int = 10,
    ) -> list[StockPerformance]:
        """Return the top-N symbols by net P&L."""
        return (await self.compute(from_date, to_date, mode, top_n=n))

    async def worst_performers(
        self,
        from_date: date,
        to_date: date,
        mode: TradingMode = TradingMode.PAPER,
        n: int = 10,
    ) -> list[StockPerformance]:
        """Return the bottom-N symbols by net P&L (worst losses first)."""
        all_results = await self.compute(from_date, to_date, mode, top_n=10_000)
        all_results.sort(key=lambda s: s.net_pnl)
        return all_results[:n]

    # ── Data collectors ───────────────────────────────────────────────────────

    async def _collect_paper(
        self,
        raw: dict,
        strat_names: dict,
        from_dt: datetime,
        to_dt: datetime,
    ) -> None:
        trades = await self._paper.list_between(from_dt, to_dt)
        for t in trades:
            sid = t.strategy_id or "unknown"
            strat_names[sid] = t.strategy_name or sid
            raw[t.symbol.upper()][sid].append(t.pnl or 0.0)

    async def _collect_backtest(
        self,
        raw: dict,
        strat_names: dict,
        from_date: date,
        to_date: date,
    ) -> None:
        runs = await self._bt_run.list_runs(limit=200)
        for run in runs:
            run_from = run.backtest_from.date() if run.backtest_from else None
            run_to = run.backtest_to.date() if run.backtest_to else None
            if run_from and run_to:
                if run_to < from_date or run_from > to_date:
                    continue
            trades = await self._bt_trade.get_all_trades_for_run(run.run_id)
            for t in trades:
                if t.trading_date:
                    td = t.trading_date.date()
                    if td < from_date or td > to_date:
                        continue
                sid = t.strategy_id or run.strategy_id or "unknown"
                strat_names[sid] = t.strategy_name or run.strategy_name or sid
                raw[t.symbol.upper()][sid].append(t.pnl or 0.0)

    async def _collect_live(
        self,
        raw: dict,
        strat_names: dict,
        from_dt: datetime,
        to_dt: datetime,
    ) -> None:
        positions = await self._live.get_closed_between(from_dt, to_dt)
        for p in positions:
            sid = getattr(p, "strategy_id", None) or "live"
            strat_names[sid] = getattr(p, "strategy_name", None) or sid
            raw[p.symbol.upper()][sid].append(p.realized_pnl or 0.0)
