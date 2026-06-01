"""
Capital efficiency engine.

Answers "How well is the system deploying its capital?"

Key metrics:
  - utilization_pct: approved_allocated / total_capital × 100
  - deployment_efficiency_pct: actual_deployed / approved_allocated × 100
  - idle_capital_pct: (total_capital - allocated) / total_capital × 100
  - ROAC (return on allocated capital): net_pnl / total_deployed
  - pnl_per_rupee_invested: net_pnl / total_deployed
  - Per-strategy ROAC breakdown

Data sources:
  - PortfolioAllocation → what capital was approved for each signal
  - PaperTrade / BacktestTrade / LivePosition → actual capital deployed + P&L
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Optional

from app.repositories.backtest_run_repository import BacktestRunRepository
from app.repositories.backtest_trade_repository import BacktestTradeRepository
from app.repositories.live_position_repository import LivePositionRepository
from app.repositories.paper_trade_repository import PaperTradeRepository
from app.repositories.portfolio_allocation_repository import PortfolioAllocationRepository
from app.schemas.performance import CapitalEfficiencyResult, PeriodLabel, TradingMode
from app.utils.logger import get_logger
from app.utils.market_time import date_to_utc_midnight

logger = get_logger(__name__)


class CapitalEfficiencyEngine:
    """
    Computes capital utilisation and return-on-allocated-capital metrics.

    Inject custom repositories for tests.
    """

    def __init__(
        self,
        allocation_repo: Optional[PortfolioAllocationRepository] = None,
        paper_repo: Optional[PaperTradeRepository] = None,
        backtest_run_repo: Optional[BacktestRunRepository] = None,
        backtest_trade_repo: Optional[BacktestTradeRepository] = None,
        live_repo: Optional[LivePositionRepository] = None,
    ) -> None:
        self._alloc = allocation_repo or PortfolioAllocationRepository()
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
        total_capital: Optional[float] = None,
    ) -> CapitalEfficiencyResult:
        """
        Compute capital efficiency metrics for the date range.

        ``total_capital`` defaults to PORTFOLIO_TOTAL_CAPITAL from settings
        when not supplied.
        """
        from app.config.settings import settings

        period = PeriodLabel.build(from_date, to_date)
        from_dt = date_to_utc_midnight(from_date)
        to_dt = date_to_utc_midnight(to_date)
        cap = total_capital or settings.PORTFOLIO_TOTAL_CAPITAL

        # ── Portfolio allocation stats ────────────────────────────────────────
        allocations = await self._alloc.get_for_date_range(from_dt, to_dt)
        total_signals = len(allocations)
        approved = [a for a in allocations if a.allocation_status.value == "APPROVED"]
        rejected = [a for a in allocations if a.allocation_status.value == "REJECTED"]
        total_allocated = round(sum(a.allocated_capital for a in approved), 4)
        approval_rate = round(len(approved) / total_signals, 6) if total_signals else 0.0

        # ── Actual capital deployed + P&L from trades ─────────────────────────
        # strategy_id → {deployed: float, pnl: float}
        strat_data: dict[str, dict[str, float]] = defaultdict(lambda: {"deployed": 0.0, "pnl": 0.0})

        total_deployed = 0.0
        total_pnl = 0.0

        if mode in (TradingMode.PAPER, TradingMode.COMBINED):
            trades = await self._paper.list_between(from_dt, to_dt)
            for t in trades:
                sid = t.strategy_id or "unknown"
                deployed = (t.entry_price or 0.0) * (t.quantity or 0)
                strat_data[sid]["deployed"] += deployed
                strat_data[sid]["pnl"] += t.pnl or 0.0
                total_deployed += deployed
                total_pnl += t.pnl or 0.0

        if mode in (TradingMode.BACKTEST, TradingMode.COMBINED):
            runs = await self._bt_run.list_runs(limit=200)
            for run in runs:
                run_from = run.backtest_from.date() if run.backtest_from else None
                run_to = run.backtest_to.date() if run.backtest_to else None
                if run_from and run_to and (run_to < from_date or run_from > to_date):
                    continue
                bt_trades = await self._bt_trade.get_all_trades_for_run(run.run_id)
                for t in bt_trades:
                    if t.trading_date:
                        td = t.trading_date.date()
                        if td < from_date or td > to_date:
                            continue
                    sid = t.strategy_id or run.strategy_id or "unknown"
                    deployed = t.capital_used or 0.0
                    strat_data[sid]["deployed"] += deployed
                    strat_data[sid]["pnl"] += t.pnl or 0.0
                    total_deployed += deployed
                    total_pnl += t.pnl or 0.0

        if mode in (TradingMode.LIVE, TradingMode.COMBINED):
            positions = await self._live.get_closed_between(from_dt, to_dt)
            for p in positions:
                sid = getattr(p, "strategy_id", None) or "live"
                deployed = (p.average_price or 0.0) * (p.quantity or 0)
                strat_data[sid]["deployed"] += deployed
                strat_data[sid]["pnl"] += p.realized_pnl or 0.0
                total_deployed += deployed
                total_pnl += p.realized_pnl or 0.0

        # ── Derived metrics ───────────────────────────────────────────────────
        utilization = round(total_allocated / cap * 100, 4) if cap else 0.0
        deployment_eff = round(total_deployed / total_allocated * 100, 4) if total_allocated else 0.0
        idle_pct = round((cap - total_allocated) / cap * 100, 4) if cap else 0.0
        roac = round(total_pnl / total_deployed, 6) if total_deployed else 0.0
        pnl_per_rupee = roac

        strategy_efficiency: dict = {}
        for sid, data in strat_data.items():
            dep = data["deployed"]
            pnl = data["pnl"]
            strategy_efficiency[sid] = {
                "deployed": round(dep, 4),
                "pnl": round(pnl, 4),
                "roac": round(pnl / dep, 6) if dep else 0.0,
            }

        logger.info(
            "[capital-eff] %s mode=%s utilization=%.1f%% roac=%.4f",
            period.label, mode.value, utilization, roac,
        )
        return CapitalEfficiencyResult(
            mode=mode,
            period=period,
            total_capital=cap,
            total_allocated=total_allocated,
            total_deployed=round(total_deployed, 4),
            total_net_pnl=round(total_pnl, 4),
            utilization_pct=utilization,
            deployment_efficiency_pct=deployment_eff,
            idle_capital_pct=idle_pct,
            roac=roac,
            pnl_per_rupee_invested=pnl_per_rupee,
            strategy_efficiency=strategy_efficiency,
            total_signals=total_signals,
            approved_signals=len(approved),
            rejected_signals=len(rejected),
            approval_rate=approval_rate,
        )
