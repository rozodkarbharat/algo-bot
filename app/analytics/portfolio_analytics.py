"""
Portfolio analytics engine.

Computes full portfolio-level performance attribution:
  - Total return, drawdown, Sharpe, volatility, rolling performance
  - Per-strategy contribution to portfolio P&L
  - Top / worst contributing stocks
  - Risk attribution: which strategy adds the most volatility

This engine composes StrategyAttributionEngine and StockAttributionEngine
rather than duplicating their logic.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from app.analytics.math_helpers import (
    contribution_pct,
    cumulative_pnl_series,
    daily_pnl_series,
    max_drawdown,
    rolling_sharpe,
    sharpe_ratio,
    volatility_annual,
    win_rate as compute_win_rate,
)
from app.analytics.stock_attribution import StockAttributionEngine
from app.analytics.strategy_attribution import StrategyAttributionEngine
from app.repositories.backtest_run_repository import BacktestRunRepository
from app.repositories.backtest_trade_repository import BacktestTradeRepository
from app.repositories.live_position_repository import LivePositionRepository
from app.repositories.paper_trade_repository import PaperTradeRepository
from app.schemas.performance import (
    PeriodLabel,
    PortfolioAttribution,
    RiskContribution,
    StrategyContribution,
    TradingMode,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PortfolioAnalyticsEngine:
    """
    Full portfolio attribution engine.

    Composes the strategy and stock engines for a single, consistent view.
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
        self._strategy_engine = StrategyAttributionEngine(**kwargs)
        self._stock_engine = StockAttributionEngine(**kwargs)

    # ── Public API ────────────────────────────────────────────────────────────

    async def compute(
        self,
        from_date: date,
        to_date: date,
        mode: TradingMode = TradingMode.PAPER,
        top_stocks: int = 10,
    ) -> PortfolioAttribution:
        """
        Full portfolio attribution for a date range.

        Runs strategy + stock attribution, then derives aggregate metrics.
        """
        period = PeriodLabel.build(from_date, to_date)

        # ── Per-strategy performance ──────────────────────────────────────────
        strategies = await self._strategy_engine.compute(from_date, to_date, mode)

        # ── Portfolio-level P&L from strategy daily series ────────────────────
        # Merge all strategy daily P&L into one portfolio series.
        merged_daily: dict[date, float] = {}
        for sp in strategies:
            for date_str, pnl in sp.daily_pnl.items():
                try:
                    d = date.fromisoformat(date_str)
                except ValueError:
                    continue
                merged_daily[d] = round(merged_daily.get(d, 0.0) + pnl, 4)

        cum = cumulative_pnl_series(merged_daily)
        dd_abs, dd_pct = max_drawdown(cum)
        daily_values = list(merged_daily.values())
        sr = sharpe_ratio(daily_values)
        vol = volatility_annual(daily_values)

        total_pnl = round(sum(sp.net_pnl for sp in strategies), 4)
        total_trades = sum(sp.total_trades for sp in strategies)
        total_wins = sum(sp.wins for sp in strategies)
        overall_wr = compute_win_rate(total_wins, total_trades)

        # ── Strategy contribution list ────────────────────────────────────────
        strat_contribs = [
            StrategyContribution(
                strategy_id=sp.strategy_id,
                strategy_name=sp.strategy_name,
                net_pnl=sp.net_pnl,
                contribution_pct=contribution_pct(sp.net_pnl, total_pnl),
                trade_count=sp.total_trades,
                win_rate=sp.win_rate,
            )
            for sp in strategies
        ]
        strat_contribs.sort(key=lambda c: c.contribution_pct, reverse=True)

        # ── Risk attribution ──────────────────────────────────────────────────
        total_vol = sum(sp.volatility for sp in strategies) or 1.0
        risk_contribs = [
            RiskContribution(
                strategy_id=sp.strategy_id,
                max_drawdown=sp.max_drawdown,
                sharpe_ratio=sp.sharpe_ratio,
                volatility=sp.volatility,
                contribution_to_portfolio_risk_pct=contribution_pct(sp.volatility, total_vol),
            )
            for sp in strategies
        ]
        risk_contribs.sort(key=lambda r: r.contribution_to_portfolio_risk_pct, reverse=True)

        # ── Stock attribution ─────────────────────────────────────────────────
        all_stocks = await self._stock_engine.compute(from_date, to_date, mode, top_n=10_000)
        top = all_stocks[:top_stocks]
        worst_by_pnl = sorted(all_stocks, key=lambda s: s.net_pnl)[:top_stocks]

        logger.info(
            "[portfolio-analytics] %s mode=%s total_pnl=%.2f strategies=%d",
            period.label, mode.value, total_pnl, len(strategies),
        )
        return PortfolioAttribution(
            mode=mode,
            period=period,
            total_portfolio_pnl=total_pnl,
            total_trades=total_trades,
            overall_win_rate=overall_wr,
            overall_sharpe=sr,
            overall_max_drawdown=dd_abs,
            overall_max_drawdown_pct=dd_pct,
            overall_volatility=vol,
            strategy_contributions=strat_contribs,
            top_stocks=top,
            worst_stocks=worst_by_pnl,
            risk_contributions=risk_contribs,
            daily_pnl={str(k): v for k, v in merged_daily.items()},
            cumulative_pnl=cum,
            updated_at=_utcnow(),
        )

    async def rolling_performance(
        self,
        from_date: date,
        to_date: date,
        mode: TradingMode = TradingMode.PAPER,
        window: int = 20,
    ) -> dict[str, dict[str, float]]:
        """
        Rolling Sharpe over a sliding window.

        Returns ``{"rolling_sharpe": {date_str: sharpe_value}}``.
        """
        strategies = await self._strategy_engine.compute(from_date, to_date, mode)
        merged_daily: dict[date, float] = {}
        for sp in strategies:
            for date_str, pnl in sp.daily_pnl.items():
                try:
                    d = date.fromisoformat(date_str)
                except ValueError:
                    continue
                merged_daily[d] = round(merged_daily.get(d, 0.0) + pnl, 4)

        roll = rolling_sharpe(merged_daily, window=window)
        return {"rolling_sharpe": {str(k): v for k, v in roll.items()}}
