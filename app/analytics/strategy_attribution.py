"""
Strategy attribution engine.

Aggregates trade-level data from paper / backtest / live sources and computes
per-strategy performance metrics for a given date range.

Data sources:
  PAPER     → PaperTrade collection (filtered by trading_date range + strategy_id)
  BACKTEST  → BacktestTrade + BacktestMetrics (all runs whose date range overlaps)
  LIVE      → LivePosition (closed, filtered by trading_date range)
  COMBINED  → all three above merged

Design:
  - All database I/O is done via injected repositories.
  - All math is delegated to math_helpers.
  - Returns a list[StrategyPerformance] — one per distinct strategy_id found.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from app.analytics.math_helpers import (
    avg_win_avg_loss,
    cumulative_pnl_series,
    daily_pnl_series,
    expectancy,
    max_drawdown,
    profit_factor,
    sharpe_ratio,
    volatility_annual,
    win_rate as compute_win_rate,
)
from app.repositories.backtest_metrics_repository import BacktestMetricsRepository
from app.repositories.backtest_run_repository import BacktestRunRepository
from app.repositories.backtest_trade_repository import BacktestTradeRepository
from app.repositories.live_position_repository import LivePositionRepository
from app.repositories.paper_trade_repository import PaperTradeRepository
from app.schemas.performance import (
    PeriodLabel,
    StrategyPerformance,
    TradingMode,
)
from app.utils.logger import get_logger
from app.utils.market_time import date_to_utc_midnight

logger = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StrategyAttributionEngine:
    """
    Computes per-strategy performance attribution from trade records.

    Inject custom repositories for tests.
    """

    def __init__(
        self,
        paper_repo: Optional[PaperTradeRepository] = None,
        backtest_run_repo: Optional[BacktestRunRepository] = None,
        backtest_trade_repo: Optional[BacktestTradeRepository] = None,
        backtest_metrics_repo: Optional[BacktestMetricsRepository] = None,
        live_repo: Optional[LivePositionRepository] = None,
    ) -> None:
        self._paper = paper_repo or PaperTradeRepository()
        self._bt_run = backtest_run_repo or BacktestRunRepository()
        self._bt_trade = backtest_trade_repo or BacktestTradeRepository()
        self._bt_metrics = backtest_metrics_repo or BacktestMetricsRepository()
        self._live = live_repo or LivePositionRepository()

    # ── Public API ────────────────────────────────────────────────────────────

    async def compute(
        self,
        from_date: date,
        to_date: date,
        mode: TradingMode = TradingMode.PAPER,
        strategy_id: Optional[str] = None,
    ) -> list[StrategyPerformance]:
        """
        Compute per-strategy performance for the given date range.

        Returns one StrategyPerformance per distinct strategy_id found.
        Pass strategy_id to restrict to a single strategy.
        """
        period = PeriodLabel.build(from_date, to_date)
        from_dt = date_to_utc_midnight(from_date)
        to_dt = date_to_utc_midnight(to_date)

        raw: dict[str, list[_TradeSummary]] = {}

        if mode in (TradingMode.PAPER, TradingMode.COMBINED):
            await self._collect_paper(raw, from_dt, to_dt)
        if mode in (TradingMode.BACKTEST, TradingMode.COMBINED):
            await self._collect_backtest(raw, from_date, to_date)
        if mode in (TradingMode.LIVE, TradingMode.COMBINED):
            await self._collect_live(raw, from_dt, to_dt)

        if strategy_id:
            raw = {k: v for k, v in raw.items() if k == strategy_id}

        results = []
        for strat_id, trades in raw.items():
            perf = self._build_performance(strat_id, trades, mode, period)
            results.append(perf)

        results.sort(key=lambda p: p.net_pnl, reverse=True)
        logger.info(
            "[strategy-attr] %s mode=%s strategies=%d",
            period.label, mode.value, len(results),
        )
        return results

    # ── Data collectors ───────────────────────────────────────────────────────

    async def _collect_paper(
        self,
        raw: dict[str, list["_TradeSummary"]],
        from_dt: datetime,
        to_dt: datetime,
    ) -> None:
        trades = await self._paper.list_between(from_dt, to_dt)
        for t in trades:
            strat = t.strategy_id or "unknown"
            capital = (t.entry_price or 0.0) * (t.quantity or 0)
            brokerage = (t.brokerage or 0.0) * 2  # entry + exit side
            gross_pnl = (t.pnl or 0.0) + brokerage
            raw.setdefault(strat, []).append(
                _TradeSummary(
                    strategy_id=strat,
                    strategy_name=t.strategy_name or strat,
                    trade_date=t.trading_date.date() if t.trading_date else from_dt.date(),
                    net_pnl=t.pnl or 0.0,
                    gross_pnl=gross_pnl,
                    brokerage=brokerage,
                    capital_used=capital,
                )
            )

    async def _collect_backtest(
        self,
        raw: dict[str, list["_TradeSummary"]],
        from_date: date,
        to_date: date,
    ) -> None:
        runs = await self._bt_run.list_runs(limit=200)
        for run in runs:
            # Overlap check: run date range overlaps query range
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
                strat = t.strategy_id or run.strategy_id or "unknown"
                gross = (t.pnl or 0.0)
                brokerage = 0.0  # backtest tracks slippage in pnl already
                raw.setdefault(strat, []).append(
                    _TradeSummary(
                        strategy_id=strat,
                        strategy_name=t.strategy_name or run.strategy_name or strat,
                        trade_date=t.trading_date.date() if t.trading_date else from_date,
                        net_pnl=t.pnl or 0.0,
                        gross_pnl=gross,
                        brokerage=brokerage,
                        capital_used=t.capital_used or 0.0,
                    )
                )

    async def _collect_live(
        self,
        raw: dict[str, list["_TradeSummary"]],
        from_dt: datetime,
        to_dt: datetime,
    ) -> None:
        positions = await self._live.get_closed_between(from_dt, to_dt)
        for p in positions:
            # LivePosition doesn't directly store strategy_id; use "live"
            strat = getattr(p, "strategy_id", None) or "live"
            strategy_name = getattr(p, "strategy_name", None) or strat
            capital = (p.average_price or 0.0) * (p.quantity or 0)
            raw.setdefault(strat, []).append(
                _TradeSummary(
                    strategy_id=strat,
                    strategy_name=strategy_name,
                    trade_date=p.trading_date.date() if p.trading_date else from_dt.date(),
                    net_pnl=p.realized_pnl or 0.0,
                    gross_pnl=p.realized_pnl or 0.0,
                    brokerage=0.0,
                    capital_used=capital,
                )
            )

    # ── Metric computation ────────────────────────────────────────────────────

    @staticmethod
    def _build_performance(
        strategy_id: str,
        trades: list["_TradeSummary"],
        mode: TradingMode,
        period: PeriodLabel,
    ) -> StrategyPerformance:
        if not trades:
            return StrategyPerformance(
                strategy_id=strategy_id,
                strategy_name=strategy_id,
                mode=mode,
                period=period,
            )
        strategy_name = trades[0].strategy_name

        pnls = [t.net_pnl for t in trades]
        wins = sum(1 for p in pnls if p > 0)
        losses = sum(1 for p in pnls if p <= 0)
        total = len(pnls)
        wrate = compute_win_rate(wins, total)

        net_pnl = round(sum(pnls), 4)
        gross_pnl = round(sum(t.gross_pnl for t in trades), 4)
        total_brokerage = round(sum(t.brokerage for t in trades), 4)
        avg_trade = round(net_pnl / total, 4) if total else 0.0

        avg_w, avg_l = avg_win_avg_loss(pnls)
        exp = expectancy(avg_w, avg_l, wrate)
        pf = profit_factor(
            sum(p for p in pnls if p > 0),
            sum(p for p in pnls if p <= 0),
        )

        daily = daily_pnl_series(
            [t.trade_date for t in trades],
            pnls,
        )
        cum = cumulative_pnl_series(daily)
        dd_abs, dd_pct = max_drawdown(cum)
        sr = sharpe_ratio(list(daily.values()))
        vol = volatility_annual(list(daily.values()))

        return StrategyPerformance(
            strategy_id=strategy_id,
            strategy_name=strategy_name,
            mode=mode,
            period=period,
            total_trades=total,
            wins=wins,
            losses=losses,
            win_rate=wrate,
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            total_brokerage=total_brokerage,
            avg_trade_pnl=avg_trade,
            expectancy=exp,
            sharpe_ratio=sr,
            max_drawdown=dd_abs,
            max_drawdown_pct=dd_pct,
            profit_factor=pf,
            volatility=vol,
            daily_pnl={str(k): v for k, v in daily.items()},
            cumulative_pnl=cum,
            updated_at=_utcnow(),
        )


# ── Internal DTO ──────────────────────────────────────────────────────────────

class _TradeSummary:
    __slots__ = (
        "strategy_id", "strategy_name", "trade_date",
        "net_pnl", "gross_pnl", "brokerage", "capital_used",
    )

    def __init__(
        self,
        strategy_id: str,
        strategy_name: str,
        trade_date: date,
        net_pnl: float,
        gross_pnl: float,
        brokerage: float,
        capital_used: float,
    ) -> None:
        self.strategy_id = strategy_id
        self.strategy_name = strategy_name
        self.trade_date = trade_date
        self.net_pnl = net_pnl
        self.gross_pnl = gross_pnl
        self.brokerage = brokerage
        self.capital_used = capital_used
