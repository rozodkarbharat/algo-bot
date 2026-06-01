"""
Monte Carlo Risk Analysis — request and response Pydantic schemas.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ── Request ───────────────────────────────────────────────────────────────────

class MonteCarloRunRequest(BaseModel):
    strategy_ids:     list[str]          = Field(
        ...,
        description="One or more strategy IDs to analyse (e.g. ['one_side_orb', 'orhv']).",
    )
    simulation_count: int                = Field(
        default=1000, ge=100, le=10000,
        description="Number of Monte Carlo simulations to run.",
    )
    starting_capital: float              = Field(
        default=1_000_000.0, gt=0,
        description="Starting account equity in ₹.",
    )
    sampling_method: str                 = Field(
        default="bootstrap",
        description="Sampling strategy: 'bootstrap', 'random_shuffle', or 'replacement'.",
    )
    ruin_thresholds: list[float]         = Field(
        default=[0.50, 0.40, 0.30],
        description=(
            "Ruin threshold fractions. 0.50 = account falls to ≤50% of starting capital. "
            "Must be in (0, 1)."
        ),
    )
    confidence_levels: list[float]       = Field(
        default=[0.90, 0.95, 0.99],
        description="Confidence levels for streak confidence-interval calculations.",
    )
    backtest_run_ids: Optional[list[str]] = Field(
        default=None,
        description=(
            "Specific backtest run IDs to source trades from. "
            "If None, all completed backtest trades for the given strategies are used."
        ),
    )
    seed: Optional[int]                  = Field(
        default=None,
        description="Optional random seed for reproducible simulations.",
    )


# ── Result response ───────────────────────────────────────────────────────────

class MonteCarloResultResponse(BaseModel):
    result_id:          str
    run_id:             str
    strategy_id:        str
    avg_return:         float
    median_return:      float
    best_return:        float
    worst_return:       float
    std_return:         float
    avg_drawdown:       float
    max_drawdown:       float
    probability_of_ruin:          dict
    avg_consecutive_losses:       float
    max_consecutive_losses:       int
    return_percentiles:           dict
    drawdown_percentiles:         dict
    streak_confidence_intervals:  dict
    capital_requirements:         dict
    trade_count:       int
    simulation_count:  int
    starting_capital:  float
    created_at:        datetime


# ── Run response ──────────────────────────────────────────────────────────────

class MonteCarloRunResponse(BaseModel):
    run_id:           str
    strategy_ids:     list[str]
    simulation_count: int
    status:           str
    started_at:       Optional[datetime]
    completed_at:     Optional[datetime]
    configuration:    dict
    error_message:    Optional[str]
    created_at:       datetime


class MonteCarloRunDetailResponse(MonteCarloRunResponse):
    metadata:  dict
    results:   list[MonteCarloResultResponse] = Field(default_factory=list)


# ── Report response ───────────────────────────────────────────────────────────

class MonteCarloReportResponse(BaseModel):
    run_id:            str
    risk_reports:      dict  # strategy_id → risk_report dict
    drawdown_reports:  dict  # strategy_id → drawdown_report dict
    capital_reports:   dict  # strategy_id → capital_requirement_report dict
    comparison_report: dict  # combined strategy_comparison_report
    generated_at:      datetime
