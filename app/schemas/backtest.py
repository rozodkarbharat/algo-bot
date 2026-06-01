"""
Pydantic API schemas for the backtesting engine endpoints.

Decouples the HTTP API surface from MongoDB document structure.
All date fields use Python date/datetime for automatic JSON serialisation.
"""

from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Request schemas ───────────────────────────────────────────────────────────

class BacktestRunRequest(BaseModel):
    """Request body for POST /api/v1/backtest/run."""

    strategy_id: str = Field(
        default="one_side_orb",
        description="Strategy to backtest.  See GET /api/v1/strategies for available IDs.",
    )
    from_date: date = Field(..., description="Backtest start date (inclusive)")
    to_date: date = Field(..., description="Backtest end date (inclusive)")

    symbols: Optional[list[str]] = Field(
        default=None,
        description="Symbols to test; omit for all active NIFTY50 stocks",
    )
    probability_threshold: float = Field(
        default=0.70,
        ge=0.0,
        le=1.0,
        description="Minimum continuation probability for symbol inclusion",
    )
    max_orb_range_pct: float = Field(
        default=1.0,
        gt=0.0,
        le=5.0,
        description="Skip execution-day setups where first-candle range > this %",
    )
    max_entry_time_ist: str = Field(
        default="11:30",
        description="Latest 15-min candle open time (HH:MM IST) for entry",
    )
    capital_per_trade: float = Field(
        default=100_000.0,
        gt=0,
        description="Capital allocated per simulated trade (₹)",
    )
    slippage_pct: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="Slippage % applied to fills",
    )
    brokerage_per_side: float = Field(
        default=20.0,
        ge=0.0,
        description="Flat brokerage per trade side (₹)",
    )
    sl_buffer_pct: float = Field(
        default=0.0,
        ge=0.0,
        le=2.0,
        description="Additional SL buffer % beyond the ORB boundary",
    )


# ── Run response schemas ──────────────────────────────────────────────────────

class BacktestRunResponse(BaseModel):
    """API representation of a BacktestRun document."""

    run_id: str
    strategy_id: str = "one_side_orb"
    strategy_name: str
    strategy_version: str = "1.0.0"
    status: str
    symbols: list[str]
    backtest_from: Optional[date]
    backtest_to: Optional[date]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    configuration: dict
    summary_metrics: dict
    error_message: Optional[str]
    created_at: datetime


# ── Trade response schemas ────────────────────────────────────────────────────

class BacktestTradeResponse(BaseModel):
    """API representation of a single BacktestTrade document."""

    run_id: str
    symbol: str
    trading_date: date
    trade_side: str
    breakout_side: str
    orb_high: float
    orb_low: float
    probability_score: float

    entry_time: Optional[datetime]
    entry_price: Optional[float]
    stop_loss: float

    exit_time: Optional[datetime]
    exit_price: Optional[float]
    exit_reason: str

    quantity: int
    capital_used: float
    pnl: float
    pnl_percent: float
    risk_reward: Optional[float]
    metadata: dict


# ── Metrics response schemas ──────────────────────────────────────────────────

class BacktestMetricsResponse(BaseModel):
    """API representation of a BacktestMetrics document."""

    run_id: str

    # Counts
    total_trades: int
    winning_trades: int
    losing_trades: int
    no_entry_days: int
    total_candidate_days: int

    # Rates
    win_rate: float
    sl_hit_rate: float
    breakout_success_rate: float

    # P&L
    total_pnl: float
    avg_pnl_per_trade: float
    avg_win: float
    avg_loss: float
    max_win: float
    max_loss: float

    # Risk
    max_drawdown: float
    max_drawdown_percent: float
    profit_factor: float
    expectancy: float
    sharpe_ratio: Optional[float]
    avg_risk_reward: Optional[float]

    # Consecutive
    max_consecutive_wins: int
    max_consecutive_losses: int

    # Breakdowns
    per_symbol_metrics: dict
    daily_pnl: dict
    monthly_pnl: dict

    created_at: datetime


# ── Analytics response schemas ────────────────────────────────────────────────

class SymbolPerformanceResponse(BaseModel):
    symbol: str
    total_trades: int
    wins: int
    losses: int
    total_pnl: float
    win_rate: float
    avg_pnl: float
    best_trade: float
    worst_trade: float


class EntryTimeSlotResponse(BaseModel):
    time_ist: str
    total_entries: int
    wins: int
    win_rate: float
    avg_pnl: float
    total_pnl: float


class BacktestAnalyticsResponse(BaseModel):
    """API representation of BacktestAnalyticsResult."""

    run_id: str
    best_symbols: list[SymbolPerformanceResponse]
    worst_symbols: list[SymbolPerformanceResponse]
    entry_time_analysis: list[EntryTimeSlotResponse]
    long_metrics: dict
    short_metrics: dict
    monthly_pnl_heatmap: dict
    orb_range_buckets: list[dict]
    probability_sensitivity: list[dict]
    metadata: dict
