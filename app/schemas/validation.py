"""
Pydantic v2 response schemas for the Live Validation & Reality Gap Analysis API.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


# ── Signal Quality ────────────────────────────────────────────────────────────

class StrategySignalQualitySchema(BaseModel):
    strategy_id: str
    generated: int
    executed: int
    missed: int
    conversion_rate: float


class SignalQualityResponse(BaseModel):
    generated_count: int
    executed_count: int
    missed_count: int
    conversion_rate: float
    miss_reasons: dict[str, int]
    by_strategy: list[StrategySignalQualitySchema]
    sample_days: int
    from_date: datetime
    to_date: datetime


# ── Slippage ──────────────────────────────────────────────────────────────────

class SymbolSlippageSchema(BaseModel):
    symbol: str
    avg_entry_slippage_bps: float
    avg_exit_slippage_bps: float
    worst_entry_slippage_bps: float
    worst_exit_slippage_bps: float
    total_slippage_cost_inr: float
    trade_count: int


class SlippageResponse(BaseModel):
    avg_entry_slippage_bps: float
    avg_exit_slippage_bps: float
    worst_entry_slippage_bps: float
    worst_exit_slippage_bps: float
    total_slippage_cost_inr: float
    symbol_breakdown: list[SymbolSlippageSchema]
    sample_count: int
    trading_mode: str
    from_date: datetime
    to_date: datetime


# ── Latency ───────────────────────────────────────────────────────────────────

class LatencyPercentilesSchema(BaseModel):
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float


class LatencyResponse(BaseModel):
    avg_signal_latency_ms: float
    signal_latency_percentiles: LatencyPercentilesSchema
    avg_execution_latency_ms: float
    execution_latency_percentiles: LatencyPercentilesSchema
    avg_ws_latency_ms: Optional[float]
    ws_latency_percentiles: Optional[LatencyPercentilesSchema]
    sample_count: int
    high_latency_signals: list[dict]
    from_date: datetime
    to_date: datetime


# ── Reality Gap ───────────────────────────────────────────────────────────────

class ModeMetricsSchema(BaseModel):
    mode: str
    win_rate: float
    avg_pnl_per_trade: float
    total_pnl: float
    max_drawdown: float
    expectancy: float
    trade_count: int
    sharpe_ratio: Optional[float]


class RealityGapResponse(BaseModel):
    backtest: Optional[ModeMetricsSchema]
    paper: Optional[ModeMetricsSchema]
    live: Optional[ModeMetricsSchema]
    paper_win_rate_gap: Optional[float]
    paper_pnl_gap: Optional[float]
    paper_drawdown_gap: Optional[float]
    paper_expectancy_gap: Optional[float]
    live_win_rate_gap: Optional[float]
    live_pnl_gap: Optional[float]
    live_drawdown_gap: Optional[float]
    live_expectancy_gap: Optional[float]
    live_vs_paper_win_rate_gap: Optional[float]
    live_vs_paper_pnl_gap: Optional[float]
    strategy_id: str
    analysis_period_days: int
    from_date: datetime
    to_date: datetime


# ── Health Score ──────────────────────────────────────────────────────────────

class HealthDimensionSchema(BaseModel):
    name: str
    score: float
    weight: float
    weighted_score: float
    detail: str


class StrategyHealthSchema(BaseModel):
    strategy_id: str
    overall_score: float
    grade: str
    signal_quality_score: float
    execution_quality_score: float
    pnl_stability_score: float
    slippage_score: float
    dimensions: list[HealthDimensionSchema]
    confidence: str
    sample_trades: int
    recommendation: str


class HealthResponse(BaseModel):
    strategies: list[StrategyHealthSchema]
    from_date: datetime
    to_date: datetime
