"""
Pydantic v2 schemas for the Performance Attribution & Strategy Comparison API.

These are pure response models — NOT Beanie Documents. They are computed
on-demand from existing trade / position / allocation collections and
returned as JSON.  The source-of-truth is always the underlying trade data;
these schemas carry only the derived analytics.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Optional

from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────────────────

class TradingMode(StrEnum):
    """Source dataset for attribution computation."""

    PAPER = "paper"
    BACKTEST = "backtest"
    LIVE = "live"
    COMBINED = "combined"


# ── Shared primitives ─────────────────────────────────────────────────────────

class PeriodLabel(BaseModel):
    """Human-readable period descriptor included in every attribution response."""

    from_date: date
    to_date: date
    label: str = Field(description="e.g. '2025-01-01 to 2025-03-31'")

    @classmethod
    def build(cls, from_date: date, to_date: date) -> "PeriodLabel":
        return cls(
            from_date=from_date,
            to_date=to_date,
            label=f"{from_date} to {to_date}",
        )


# ── STEP 1 MODELS ─────────────────────────────────────────────────────────────

class StrategyPerformance(BaseModel):
    """
    Aggregated performance for one strategy over a period.

    Covers any combination of paper / backtest / live trades depending on
    the ``mode`` parameter supplied to the attribution engine.
    """

    strategy_id: str
    strategy_name: str
    mode: TradingMode
    period: PeriodLabel

    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0          # [0.0, 1.0]

    gross_pnl: float = 0.0         # before brokerage deduction
    net_pnl: float = 0.0           # after brokerage + slippage
    total_brokerage: float = 0.0
    avg_trade_pnl: float = 0.0

    expectancy: float = 0.0        # expected ₹ per trade
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0      # ₹ peak-to-trough
    max_drawdown_pct: float = 0.0  # % of peak equity
    profit_factor: float = 0.0
    volatility: float = 0.0        # annualised σ of daily P&L

    # Daily P&L series for charting (date string → ₹)
    daily_pnl: dict = Field(default_factory=dict)
    cumulative_pnl: list[float] = Field(default_factory=list)

    updated_at: datetime = Field(default_factory=datetime.utcnow)


class StockContributionBreakdown(BaseModel):
    """Per-strategy P&L breakdown for one stock."""

    strategy_id: str
    strategy_name: str
    trades: int
    net_pnl: float
    win_rate: float


class StockPerformance(BaseModel):
    """
    Aggregated performance for one symbol over a period.

    ``contribution_pct`` shows what fraction of the total portfolio P&L this
    symbol was responsible for — useful for leaderboards.
    ``consistency_score`` combines win_rate with trade volume to reward
    consistent performers vs one-hit-wonders.
    """

    symbol: str
    mode: TradingMode
    period: PeriodLabel

    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    net_pnl: float = 0.0
    avg_pnl: float = 0.0
    expectancy: float = 0.0

    contribution_pct: float = 0.0  # % of total portfolio P&L
    consistency_score: float = 0.0 # win_rate × log2(trades + 1)

    # How this symbol's P&L breaks down across strategies
    strategy_breakdown: list[StockContributionBreakdown] = Field(default_factory=list)


class StrategyContribution(BaseModel):
    """One strategy's contribution to total portfolio P&L."""

    strategy_id: str
    strategy_name: str
    net_pnl: float
    contribution_pct: float
    trade_count: int
    win_rate: float


class RiskContribution(BaseModel):
    """Risk metrics per strategy for the portfolio attribution."""

    strategy_id: str
    max_drawdown: float
    sharpe_ratio: float
    volatility: float
    contribution_to_portfolio_risk_pct: float


class PortfolioAttribution(BaseModel):
    """
    Full portfolio-level attribution for a date range.

    Combines results across all strategies and stocks to show the overall
    picture: what's making money, what's dragging performance, and how risk
    is distributed.
    """

    mode: TradingMode
    period: PeriodLabel

    total_portfolio_pnl: float = 0.0
    total_trades: int = 0
    overall_win_rate: float = 0.0
    overall_sharpe: float = 0.0
    overall_max_drawdown: float = 0.0
    overall_max_drawdown_pct: float = 0.0
    overall_volatility: float = 0.0

    # Per-strategy breakdown
    strategy_contributions: list[StrategyContribution] = Field(default_factory=list)

    # Top / bottom stocks (up to 10 each)
    top_stocks: list[StockPerformance] = Field(default_factory=list)
    worst_stocks: list[StockPerformance] = Field(default_factory=list)

    # Per-strategy risk attribution
    risk_contributions: list[RiskContribution] = Field(default_factory=list)

    # Daily portfolio P&L for charting
    daily_pnl: dict = Field(default_factory=dict)
    cumulative_pnl: list[float] = Field(default_factory=list)

    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ── Capital efficiency ────────────────────────────────────────────────────────

class CapitalEfficiencyResult(BaseModel):
    """
    How effectively the system deployed its allocated capital.

    Key question: "For every ₹ committed, how much P&L was generated?"
    """

    mode: TradingMode
    period: PeriodLabel

    total_capital: float = 0.0          # PORTFOLIO_TOTAL_CAPITAL (₹)
    total_allocated: float = 0.0        # sum of approved allocation capitals (₹)
    total_deployed: float = 0.0         # sum of actual capital used in trades (₹)
    total_net_pnl: float = 0.0

    utilization_pct: float = 0.0        # total_allocated / total_capital × 100
    deployment_efficiency_pct: float = 0.0  # total_deployed / total_allocated × 100
    idle_capital_pct: float = 0.0       # (total_capital - total_allocated) / total_capital × 100

    roac: float = 0.0                   # return on allocated capital = net_pnl / total_deployed
    pnl_per_rupee_invested: float = 0.0 # net_pnl / total_deployed

    # Per-strategy efficiency breakdown
    strategy_efficiency: dict = Field(
        default_factory=dict,
        description="strategy_id → {allocated, deployed, pnl, roac}",
    )

    # Approval rate from portfolio layer
    total_signals: int = 0
    approved_signals: int = 0
    rejected_signals: int = 0
    approval_rate: float = 0.0


# ── Strategy Comparison ───────────────────────────────────────────────────────

class StrategyComparisonRow(BaseModel):
    """One strategy's metrics for side-by-side comparison."""

    strategy_id: str
    strategy_name: str
    total_trades: int
    win_rate: float
    net_pnl: float
    expectancy: float
    sharpe_ratio: float
    max_drawdown: float
    profit_factor: float
    volatility: float
    # Rank on each dimension (1 = best)
    ranks: dict = Field(default_factory=dict)


class StrategyComparisonResult(BaseModel):
    """Side-by-side comparison of two or more strategies."""

    mode: TradingMode
    period: PeriodLabel
    strategies: list[StrategyComparisonRow]
    best_by_pnl: str = ""
    best_by_sharpe: str = ""
    best_by_win_rate: str = ""
    best_by_expectancy: str = ""
    lowest_drawdown: str = ""


class PeriodSlice(BaseModel):
    """Metrics for one half of a period comparison."""

    from_date: date
    to_date: date
    label: str
    total_trades: int
    win_rate: float
    net_pnl: float
    expectancy: float
    sharpe_ratio: float
    max_drawdown: float


class PeriodComparisonResult(BaseModel):
    """Same strategy compared across two non-overlapping periods."""

    strategy_id: str
    strategy_name: str
    mode: TradingMode
    period_a: PeriodSlice
    period_b: PeriodSlice
    # Deltas: period_b − period_a
    delta_pnl: float = 0.0
    delta_win_rate: float = 0.0
    delta_expectancy: float = 0.0
    delta_sharpe: float = 0.0
    improving: bool = False    # True when period_b net_pnl > period_a


class PaperVsLiveResult(BaseModel):
    """Paper-trading metrics vs live-trading metrics for the same strategy+period."""

    strategy_id: str
    strategy_name: str
    period: PeriodLabel
    paper: Optional[StrategyPerformance] = None
    live: Optional[StrategyPerformance] = None
    # Differences (live − paper); None when either leg is absent
    slippage_impact: Optional[float] = None  # live_net_pnl − paper_net_pnl
    win_rate_delta: Optional[float] = None
    expectancy_delta: Optional[float] = None
