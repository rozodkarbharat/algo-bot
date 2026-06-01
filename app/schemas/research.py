"""
Pydantic API schemas for the research and optimization endpoints.

Decouples HTTP API surface from MongoDB document structure.
All date fields use Python date/datetime for automatic JSON serialisation.
"""

from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Request schemas ───────────────────────────────────────────────────────────

class ResearchRunRequest(BaseModel):
    """Request body for POST /api/v1/research/run."""

    from_date: date = Field(..., description="Research / optimization start date (inclusive)")
    to_date: date = Field(..., description="Research / optimization end date (inclusive)")

    symbols: Optional[list[str]] = Field(
        default=None,
        description="Symbols to include; omit for all active NIFTY50 stocks",
    )

    # Base strategy parameters (defaults for each univariate sweep)
    base_probability_threshold: float = Field(
        default=0.70, ge=0.0, le=1.0,
        description="Base continuation probability threshold",
    )
    base_max_orb_range_pct: float = Field(
        default=1.00, gt=0.0, le=5.0,
        description="Base ORB range filter (%)",
    )
    base_max_entry_time_ist: str = Field(
        default="11:30",
        description="Base entry cutoff time (IST HH:MM)",
    )
    base_sl_buffer_pct: float = Field(
        default=0.00, ge=0.0, le=2.0,
        description="Base SL buffer %",
    )
    capital_per_trade: float = Field(
        default=100_000.0, gt=0,
        description="Capital per simulated trade (₹)",
    )
    slippage_pct: float = Field(
        default=0.05, ge=0.0, le=1.0,
        description="Slippage % applied to fills",
    )
    brokerage_per_side: float = Field(
        default=20.0, ge=0.0,
        description="Flat brokerage per trade side (₹)",
    )

    # Optional sweep range overrides (omit to use engine defaults)
    probability_thresholds: Optional[list[float]] = Field(
        default=None,
        description="Custom probability threshold sweep values",
    )
    orb_range_filters: Optional[list[float]] = Field(
        default=None,
        description="Custom ORB range filter sweep values (%)",
    )
    entry_cutoff_times: Optional[list[str]] = Field(
        default=None,
        description="Custom entry cutoff time sweep values (IST HH:MM)",
    )
    sl_buffers: Optional[list[float]] = Field(
        default=None,
        description="Custom SL buffer sweep values (%)",
    )


# ── Run response schemas ──────────────────────────────────────────────────────

class ResearchRunResponse(BaseModel):
    """API representation of a ResearchRun document."""

    run_id: str
    status: str
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    configuration: dict
    error_message: Optional[str]
    created_at: datetime


class ResearchRunDetailResponse(ResearchRunResponse):
    """Extended response including run metadata (summary + report)."""

    metadata: dict


# ── Optimization result schemas ───────────────────────────────────────────────

class OptimizationResultResponse(BaseModel):
    """API representation of a ParameterOptimizationResult document."""

    run_id: str
    parameter_name: str
    parameter_value: str

    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    sl_hit_rate: float
    breakout_success_rate: float

    total_pnl: float
    avg_pnl_per_trade: float
    expectancy: float
    profit_factor: float
    max_drawdown: float
    sharpe_ratio: Optional[float]

    created_at: datetime


# ── Stock analytics schemas ───────────────────────────────────────────────────

class StockAnalyticsResponse(BaseModel):
    """API representation of a StockPerformanceAnalytics document."""

    symbol: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    sl_hit_rate: float
    breakout_success_rate: float
    total_pnl: float
    avg_pnl: float
    max_win: float
    max_loss: float
    expectancy: float
    profit_factor: float
    max_drawdown: float
    avg_orb_range_pct: float
    avg_move_after_breakout_pct: float
    best_breakout_time_range: Optional[str]
    last_run_id: Optional[str]
    updated_at: datetime


# ── Report schema ─────────────────────────────────────────────────────────────

class ResearchReportResponse(BaseModel):
    """API representation of a research report."""

    run_id: str
    executive_summary: dict
    parameter_sensitivity: dict
    stock_rankings: dict
    time_edge: dict
    market_conditions: dict
    failure_diagnostics: dict
    recommendations: list[str]
    metadata: dict
