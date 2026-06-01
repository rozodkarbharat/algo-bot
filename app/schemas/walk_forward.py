from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


class WalkForwardRunRequest(BaseModel):
    from_date: date
    to_date: date
    symbols: Optional[list[str]] = None
    training_months: int = Field(default=12, ge=3, le=60, description="Training window length in months")
    testing_months: int = Field(default=3, ge=1, le=12, description="Testing window length in months")
    step_months: int = Field(default=3, ge=1, le=12, description="Step size between windows in months")
    strategy_id: str = Field(default="one_side_orb")
    base_probability_threshold: float = Field(default=0.70, ge=0.0, le=1.0)
    base_max_orb_range_pct: float = Field(default=1.0, gt=0.0)
    base_max_entry_time_ist: str = Field(default="11:30")
    base_sl_buffer_pct: float = Field(default=0.0, ge=0.0)
    capital_per_trade: float = Field(default=100_000.0, gt=0.0)
    slippage_pct: float = Field(default=0.05, ge=0.0)
    brokerage_per_side: float = Field(default=20.0, ge=0.0)


class WalkForwardRunResponse(BaseModel):
    run_id: str
    strategy_id: str
    strategy_name: str
    status: str
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    configuration: dict
    error_message: Optional[str]
    created_at: datetime


class WalkForwardRunDetailResponse(WalkForwardRunResponse):
    metadata: dict


class WalkForwardSegmentResponse(BaseModel):
    segment_id: str
    run_id: str
    segment_number: int
    training_start: datetime
    training_end: datetime
    testing_start: datetime
    testing_end: datetime
    selected_parameters: dict
    optimization_score: float
    metrics: dict
    status: str
    error_message: Optional[str]
    created_at: datetime


class WalkForwardResultsResponse(BaseModel):
    run: WalkForwardRunDetailResponse
    segments: list[WalkForwardSegmentResponse]
    aggregated: dict
    robustness: dict
    segment_count: int
    completed_count: int
