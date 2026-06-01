"""
Portfolio & Capital Allocation API routes.

GET  /api/v1/portfolio/allocations        — list allocations (date + status filter)
GET  /api/v1/portfolio/allocations/{id}   — single allocation by allocation_id
GET  /api/v1/portfolio/risk               — current portfolio risk state
GET  /api/v1/portfolio/analytics          — portfolio performance analytics

POST /api/v1/portfolio/halt               — manually halt all allocations today
POST /api/v1/portfolio/resume             — remove halt for today
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.models.portfolio_allocation import AllocationStatus
from app.schemas.portfolio import (
    PortfolioAllocationListResponse,
    PortfolioAllocationResponse,
    PortfolioAnalyticsResponse,
    PortfolioHaltRequest,
    PortfolioRiskStateResponse,
)
from app.services.portfolio_service import portfolio_service
from app.schemas.common import MessageResponse

router = APIRouter()

_svc = portfolio_service


# ── Allocations ───────────────────────────────────────────────────────────────

@router.get(
    "/allocations",
    response_model=PortfolioAllocationListResponse,
    summary="List portfolio allocations",
    description=(
        "Return all portfolio allocation decisions for a given trading date. "
        "Optionally filter by allocation status."
    ),
)
async def list_allocations(
    trading_date: Optional[date] = Query(
        default=None,
        description="ISO date (YYYY-MM-DD). Defaults to today if omitted.",
    ),
    status: Optional[AllocationStatus] = Query(
        default=None,
        description="Filter by APPROVED | REJECTED | EXECUTED | CANCELLED",
    ),
) -> PortfolioAllocationListResponse:
    from datetime import date as _date
    from app.utils.trading_day import today_ist

    query_date = trading_date or today_ist()
    items = await _svc.get_allocations_for_date(query_date, status=status)

    if status:
        items = [a for a in items if a.allocation_status == status]

    return PortfolioAllocationListResponse(
        items=[PortfolioAllocationResponse.model_validate(a.model_dump()) for a in items],
        total=len(items),
        trading_date=str(query_date),
        status_filter=status.value if status else None,
    )


@router.get(
    "/allocations/{allocation_id}",
    response_model=PortfolioAllocationResponse,
    summary="Get a single allocation by ID",
)
async def get_allocation(allocation_id: str) -> PortfolioAllocationResponse:
    from app.models.portfolio_allocation import PortfolioAllocation

    alloc = await PortfolioAllocation.find_one({"allocation_id": allocation_id})
    if alloc is None:
        raise HTTPException(status_code=404, detail=f"Allocation not found: {allocation_id}")
    return PortfolioAllocationResponse.model_validate(alloc.model_dump())


# ── Risk state ────────────────────────────────────────────────────────────────

@router.get(
    "/risk",
    response_model=PortfolioRiskStateResponse,
    summary="Current portfolio risk state",
    description="Returns today's capital utilisation, exposure, and halt status.",
)
async def get_risk_state(
    trading_date: Optional[date] = Query(
        default=None,
        description="ISO date. Defaults to today's latest state.",
    ),
) -> PortfolioRiskStateResponse:
    state = await _svc.get_risk_state(trading_date)
    if state is None:
        raise HTTPException(
            status_code=404,
            detail="No portfolio risk state found. Portfolio may not have processed any signals yet.",
        )
    return PortfolioRiskStateResponse.from_document(state)


# ── Analytics ─────────────────────────────────────────────────────────────────

@router.get(
    "/analytics",
    response_model=PortfolioAnalyticsResponse,
    summary="Portfolio performance analytics",
    description="Aggregate metrics: approval rate, capital deployed, strategy breakdown.",
)
async def get_analytics(
    from_date: date = Query(..., description="Start date (inclusive, ISO format)"),
    to_date: date = Query(..., description="End date (inclusive, ISO format)"),
) -> PortfolioAnalyticsResponse:
    if from_date > to_date:
        raise HTTPException(
            status_code=422, detail="from_date must be on or before to_date"
        )
    analytics = await _svc.get_analytics(from_date, to_date)
    return PortfolioAnalyticsResponse(
        from_date=analytics.from_date,
        to_date=analytics.to_date,
        total_allocations=analytics.total_allocations,
        approved_allocations=analytics.approved_allocations,
        rejected_allocations=analytics.rejected_allocations,
        approval_rate=analytics.approval_rate,
        total_capital_deployed=analytics.total_capital_deployed,
        avg_capital_per_trade=analytics.avg_capital_per_trade,
        allocation_efficiency=analytics.allocation_efficiency,
        strategy_breakdown=analytics.strategy_breakdown,
        rejection_reasons=analytics.rejection_reasons,
    )


# ── Control endpoints ─────────────────────────────────────────────────────────

@router.post(
    "/halt",
    response_model=MessageResponse,
    summary="Halt all portfolio allocations for today",
    description=(
        "Blocks all new signal approvals for the current trading day. "
        "Existing open positions are unaffected. Use /resume to clear."
    ),
)
async def halt_portfolio(body: PortfolioHaltRequest) -> MessageResponse:
    await _svc.halt_today(reason=body.reason)
    return MessageResponse(message=f"Portfolio allocations halted: {body.reason}")


@router.post(
    "/resume",
    response_model=MessageResponse,
    summary="Resume portfolio allocations for today",
)
async def resume_portfolio() -> MessageResponse:
    await _svc.resume_today()
    return MessageResponse(message="Portfolio allocations resumed.")
