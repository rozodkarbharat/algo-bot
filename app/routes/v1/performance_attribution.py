"""
Performance Attribution & Strategy Comparison API routes.

GET /api/v1/analytics/strategies          — per-strategy performance
GET /api/v1/analytics/stocks              — per-symbol performance leaderboard
GET /api/v1/analytics/portfolio           — full portfolio attribution
GET /api/v1/analytics/portfolio/rolling   — rolling Sharpe chart data
GET /api/v1/analytics/capital             — capital efficiency report
GET /api/v1/analytics/comparison          — strategy vs strategy
GET /api/v1/analytics/comparison/periods  — period vs period for one strategy
GET /api/v1/analytics/comparison/paper-vs-live — paper vs live for one strategy
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.analytics.capital_efficiency import CapitalEfficiencyEngine
from app.analytics.portfolio_analytics import PortfolioAnalyticsEngine
from app.analytics.stock_attribution import StockAttributionEngine
from app.analytics.strategy_attribution import StrategyAttributionEngine
from app.analytics.strategy_comparison import StrategyComparisonEngine
from app.schemas.performance import (
    CapitalEfficiencyResult,
    PaperVsLiveResult,
    PeriodComparisonResult,
    PortfolioAttribution,
    StrategyComparisonResult,
    StrategyPerformance,
    StockPerformance,
    TradingMode,
)
from app.utils.trading_day import today_ist

router = APIRouter()

# Module-level singletons (all engines are stateless/repository-injecting)
_strategy_engine = StrategyAttributionEngine()
_stock_engine = StockAttributionEngine()
_portfolio_engine = PortfolioAnalyticsEngine()
_capital_engine = CapitalEfficiencyEngine()
_comparison_engine = StrategyComparisonEngine()


def _default_from() -> date:
    """Default start date: first day of current month."""
    t = today_ist()
    return t.replace(day=1)


# ── Strategies ────────────────────────────────────────────────────────────────

@router.get(
    "/strategies",
    response_model=list[StrategyPerformance],
    summary="Per-strategy performance attribution",
    description=(
        "Compute win rate, P&L, Sharpe, drawdown, and expectancy for each "
        "strategy over the requested date range."
    ),
)
async def get_strategy_performance(
    from_date: date = Query(default=None, description="Start date (ISO). Defaults to first day of current month."),
    to_date: date = Query(default=None, description="End date (ISO). Defaults to today."),
    mode: TradingMode = Query(default=TradingMode.PAPER, description="Data source: paper | backtest | live | combined"),
    strategy_id: Optional[str] = Query(default=None, description="Restrict to a single strategy ID"),
) -> list[StrategyPerformance]:
    from_d = from_date or _default_from()
    to_d = to_date or today_ist()
    if from_d > to_d:
        raise HTTPException(422, "from_date must be on or before to_date")
    return await _strategy_engine.compute(from_d, to_d, mode, strategy_id)


# ── Stocks ────────────────────────────────────────────────────────────────────

@router.get(
    "/stocks",
    response_model=list[StockPerformance],
    summary="Per-symbol performance leaderboard",
    description="Rank stocks by net P&L with contribution % and consistency score.",
)
async def get_stock_performance(
    from_date: date = Query(default=None),
    to_date: date = Query(default=None),
    mode: TradingMode = Query(default=TradingMode.PAPER),
    sort: str = Query(default="pnl", description="Sort field: pnl | consistency | win_rate"),
    top_n: int = Query(default=20, ge=1, le=100),
    worst: bool = Query(default=False, description="Return worst performers instead of top"),
) -> list[StockPerformance]:
    from_d = from_date or _default_from()
    to_d = to_date or today_ist()
    if from_d > to_d:
        raise HTTPException(422, "from_date must be on or before to_date")

    if worst:
        return await _stock_engine.worst_performers(from_d, to_d, mode, top_n)

    results = await _stock_engine.compute(from_d, to_d, mode, top_n=top_n)

    if sort == "consistency":
        results.sort(key=lambda s: s.consistency_score, reverse=True)
    elif sort == "win_rate":
        results.sort(key=lambda s: s.win_rate, reverse=True)
    # default: already sorted by pnl

    return results


# ── Portfolio ─────────────────────────────────────────────────────────────────

@router.get(
    "/portfolio",
    response_model=PortfolioAttribution,
    summary="Full portfolio attribution",
    description=(
        "Returns total return, drawdown, Sharpe, strategy contributions, "
        "top/worst stocks, and risk breakdown."
    ),
)
async def get_portfolio_attribution(
    from_date: date = Query(default=None),
    to_date: date = Query(default=None),
    mode: TradingMode = Query(default=TradingMode.PAPER),
    top_stocks: int = Query(default=10, ge=1, le=50),
) -> PortfolioAttribution:
    from_d = from_date or _default_from()
    to_d = to_date or today_ist()
    if from_d > to_d:
        raise HTTPException(422, "from_date must be on or before to_date")
    return await _portfolio_engine.compute(from_d, to_d, mode, top_stocks)


@router.get(
    "/portfolio/rolling",
    summary="Rolling Sharpe chart data",
    description="Returns rolling Sharpe ratio over a sliding window for charting.",
)
async def get_rolling_performance(
    from_date: date = Query(default=None),
    to_date: date = Query(default=None),
    mode: TradingMode = Query(default=TradingMode.PAPER),
    window: int = Query(default=20, ge=5, le=60),
) -> dict:
    from_d = from_date or _default_from()
    to_d = to_date or today_ist()
    if from_d > to_d:
        raise HTTPException(422, "from_date must be on or before to_date")
    return await _portfolio_engine.rolling_performance(from_d, to_d, mode, window)


# ── Capital efficiency ────────────────────────────────────────────────────────

@router.get(
    "/capital",
    response_model=CapitalEfficiencyResult,
    summary="Capital efficiency report",
    description=(
        "Shows utilization %, idle capital %, ROAC, and P&L per ₹ invested "
        "for the configured portfolio capital base."
    ),
)
async def get_capital_efficiency(
    from_date: date = Query(default=None),
    to_date: date = Query(default=None),
    mode: TradingMode = Query(default=TradingMode.PAPER),
) -> CapitalEfficiencyResult:
    from_d = from_date or _default_from()
    to_d = to_date or today_ist()
    if from_d > to_d:
        raise HTTPException(422, "from_date must be on or before to_date")
    return await _capital_engine.compute(from_d, to_d, mode)


# ── Comparison ────────────────────────────────────────────────────────────────

@router.get(
    "/comparison",
    response_model=StrategyComparisonResult,
    summary="Strategy vs strategy comparison",
    description="Side-by-side ranked comparison of two or more strategies.",
)
async def compare_strategies(
    strategy_ids: str = Query(
        ...,
        description="Comma-separated strategy IDs to compare (e.g. 'one_side_orb,orhv')",
    ),
    from_date: date = Query(default=None),
    to_date: date = Query(default=None),
    mode: TradingMode = Query(default=TradingMode.PAPER),
) -> StrategyComparisonResult:
    from_d = from_date or _default_from()
    to_d = to_date or today_ist()
    if from_d > to_d:
        raise HTTPException(422, "from_date must be on or before to_date")
    ids = [s.strip() for s in strategy_ids.split(",") if s.strip()]
    return await _comparison_engine.compare_strategies(ids, from_d, to_d, mode)


@router.get(
    "/comparison/periods",
    response_model=PeriodComparisonResult,
    summary="Period vs period comparison for one strategy",
    description="Compare the same strategy across two date ranges to track improvement.",
)
async def compare_periods(
    strategy_id: str = Query(...),
    period_a_from: date = Query(..., description="Start of period A"),
    period_a_to: date = Query(..., description="End of period A"),
    period_b_from: date = Query(..., description="Start of period B"),
    period_b_to: date = Query(..., description="End of period B"),
    mode: TradingMode = Query(default=TradingMode.PAPER),
) -> PeriodComparisonResult:
    for f, t in [(period_a_from, period_a_to), (period_b_from, period_b_to)]:
        if f > t:
            raise HTTPException(422, f"period from ({f}) must be on or before to ({t})")
    return await _comparison_engine.compare_periods(
        strategy_id,
        period_a_from, period_a_to,
        period_b_from, period_b_to,
        mode,
    )


@router.get(
    "/comparison/paper-vs-live",
    response_model=PaperVsLiveResult,
    summary="Paper trading vs live execution comparison",
    description=(
        "Compares the same strategy's paper and live metrics to quantify "
        "the slippage impact and execution quality of live trading."
    ),
)
async def compare_paper_vs_live(
    strategy_id: str = Query(...),
    from_date: date = Query(default=None),
    to_date: date = Query(default=None),
) -> PaperVsLiveResult:
    from_d = from_date or _default_from()
    to_d = to_date or today_ist()
    if from_d > to_d:
        raise HTTPException(422, "from_date must be on or before to_date")
    return await _comparison_engine.compare_paper_vs_live(strategy_id, from_d, to_d)
