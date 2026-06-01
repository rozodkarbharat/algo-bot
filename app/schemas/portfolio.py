"""
Pydantic v2 schemas for the portfolio API.

These are the HTTP request/response models for the portfolio endpoints.
They are separate from the Beanie documents so the API contract can evolve
independently of the DB schema.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models.portfolio_allocation import AllocationMethod, AllocationStatus


# ── Portfolio Allocation ──────────────────────────────────────────────────────

class PortfolioAllocationResponse(BaseModel):
    """Single allocation row returned by the API."""

    allocation_id: str
    trading_date: datetime
    strategy_id: str
    symbol: str
    signal_id: str
    signal_type: str
    entry_price: float
    stop_loss: float
    probability_score: Optional[float]

    ranking_score: float
    ranking_components: dict

    allocation_method: AllocationMethod
    allocation_percent: float
    allocated_capital: float

    allocation_status: AllocationStatus
    rejection_reason: Optional[str]
    risk_detail: dict

    sector: Optional[str]
    metadata: dict
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PortfolioAllocationListResponse(BaseModel):
    """Paginated list of allocations."""

    items: list[PortfolioAllocationResponse]
    total: int
    trading_date: Optional[str] = None
    status_filter: Optional[str] = None


# ── Portfolio Risk State ──────────────────────────────────────────────────────

class PortfolioRiskStateResponse(BaseModel):
    """Current portfolio risk snapshot."""

    trading_date: datetime
    total_capital: float
    used_capital: float
    available_capital: float
    daily_risk_used: float
    open_positions: int
    total_approved_today: int
    total_rejected_today: int
    strategy_exposure: dict
    sector_exposure: dict
    realized_pnl_today: float
    peak_capital_today: float
    is_halted: bool
    halt_reason: Optional[str]
    updated_at: datetime

    # Derived
    utilisation_pct: float = Field(
        default=0.0, description="used_capital / total_capital as a percentage"
    )

    model_config = {"from_attributes": True}

    @classmethod
    def from_document(cls, doc) -> "PortfolioRiskStateResponse":
        utilisation = (
            round(doc.used_capital / doc.total_capital * 100, 2)
            if doc.total_capital > 0
            else 0.0
        )
        return cls(
            trading_date=doc.trading_date,
            total_capital=doc.total_capital,
            used_capital=doc.used_capital,
            available_capital=doc.available_capital,
            daily_risk_used=doc.daily_risk_used,
            open_positions=doc.open_positions,
            total_approved_today=doc.total_approved_today,
            total_rejected_today=doc.total_rejected_today,
            strategy_exposure=doc.strategy_exposure,
            sector_exposure=doc.sector_exposure,
            realized_pnl_today=doc.realized_pnl_today,
            peak_capital_today=doc.peak_capital_today,
            is_halted=doc.is_halted,
            halt_reason=doc.halt_reason,
            updated_at=doc.updated_at,
            utilisation_pct=utilisation,
        )


# ── Portfolio Analytics ───────────────────────────────────────────────────────

class PortfolioAnalyticsResponse(BaseModel):
    """Aggregate performance metrics over a date range."""

    from_date: date
    to_date: date
    total_allocations: int
    approved_allocations: int
    rejected_allocations: int
    approval_rate: float
    total_capital_deployed: float
    avg_capital_per_trade: float
    allocation_efficiency: float
    strategy_breakdown: dict
    rejection_reasons: dict


# ── Control ───────────────────────────────────────────────────────────────────

class PortfolioHaltRequest(BaseModel):
    reason: str = Field(default="manual_halt", description="Reason for halting allocations")
