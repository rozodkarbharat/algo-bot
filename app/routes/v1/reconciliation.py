"""
Broker reconciliation API routes.

Endpoints:
  GET  /api/v1/reconciliation/runs            — list recent reconciliation runs
  GET  /api/v1/reconciliation/discrepancies   — list discrepancies (filterable)
  POST /api/v1/reconciliation/run             — trigger an immediate reconciliation
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status

from app.models.broker_reconciliation import DiscrepancyStatus, DiscrepancyType
from app.reconciliation.broker_reconciliation_service import broker_reconciliation_service
from app.schemas.reconciliation import (
    DiscrepancyResponse,
    ReconciliationRunResponse,
    TriggerReconciliationRequest,
    TriggerReconciliationResponse,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter()


def _run_to_response(run) -> ReconciliationRunResponse:
    return ReconciliationRunResponse(
        run_id=run.run_id,
        broker_name=run.broker_name,
        started_at=run.started_at,
        completed_at=run.completed_at,
        status=run.status,
        discrepancies_found=run.discrepancies_found,
        orders_checked=run.orders_checked,
        positions_checked=run.positions_checked,
        metadata=run.metadata,
    )


def _disc_to_response(d) -> DiscrepancyResponse:
    return DiscrepancyResponse(
        discrepancy_id=d.discrepancy_id,
        run_id=d.run_id,
        discrepancy_type=d.discrepancy_type,
        symbol=d.symbol,
        severity=d.severity,
        broker_value=d.broker_value,
        internal_value=d.internal_value,
        description=d.description,
        status=d.status,
        detected_at=d.detected_at,
        resolved_at=d.resolved_at,
        auto_resolution_attempted=d.auto_resolution_attempted,
        metadata=d.metadata,
    )


@router.get(
    "/runs",
    response_model=list[ReconciliationRunResponse],
    summary="List recent reconciliation runs",
    description=(
        "Returns the most recent broker reconciliation runs in descending "
        "order. Each run represents one complete reconciliation cycle."
    ),
)
async def list_runs(
    limit: int = Query(default=20, ge=1, le=100, description="Max runs to return"),
) -> list[ReconciliationRunResponse]:
    try:
        runs = await broker_reconciliation_service.list_runs(limit=limit)
        return [_run_to_response(r) for r in runs]
    except Exception as exc:
        logger.error("[api/recon] list_runs failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve reconciliation runs.",
        )


@router.get(
    "/discrepancies",
    response_model=list[DiscrepancyResponse],
    summary="List broker discrepancies",
    description=(
        "Returns discrepancies detected during reconciliation runs. "
        "Filter by run_id, symbol, type, or resolution status."
    ),
)
async def list_discrepancies(
    run_id: Optional[str] = Query(default=None, description="Filter by reconciliation run"),
    symbol: Optional[str] = Query(default=None, description="Filter by stock symbol"),
    discrepancy_type: Optional[DiscrepancyType] = Query(
        default=None, description="Filter by discrepancy type"
    ),
    disc_status: Optional[DiscrepancyStatus] = Query(
        default=None, alias="status", description="Filter by resolution status"
    ),
    limit: int = Query(default=100, ge=1, le=500, description="Max results to return"),
) -> list[DiscrepancyResponse]:
    try:
        discrepancies = await broker_reconciliation_service.list_discrepancies(
            run_id=run_id,
            status=disc_status,
            symbol=symbol.upper() if symbol else None,
            discrepancy_type=discrepancy_type,
            limit=limit,
        )
        return [_disc_to_response(d) for d in discrepancies]
    except Exception as exc:
        logger.error("[api/recon] list_discrepancies failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve discrepancies.",
        )


@router.post(
    "/run",
    response_model=TriggerReconciliationResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger immediate reconciliation",
    description=(
        "Runs a full broker reconciliation cycle synchronously and returns "
        "the results. This endpoint can be used to trigger reconciliation "
        "immediately after order events or for manual operator-initiated checks. "
        "Note: if LIVE_EXEC_ENABLED=False, broker-side checks are skipped and "
        "only DB-level validations (e.g. stop-loss checks) run."
    ),
)
async def trigger_reconciliation(
    request: TriggerReconciliationRequest,
) -> TriggerReconciliationResponse:
    try:
        run = await broker_reconciliation_service.run_full_reconciliation(
            broker=None,
            broker_name=request.broker_name,
            trigger=request.trigger,
        )
        return TriggerReconciliationResponse(
            run_id=run.run_id,
            status=run.status,
            discrepancies_found=run.discrepancies_found,
            orders_checked=run.orders_checked,
            positions_checked=run.positions_checked,
            message=(
                f"Reconciliation completed. "
                f"{run.discrepancies_found} discrepancy(ies) found across "
                f"{run.orders_checked} orders and {run.positions_checked} positions."
            ),
        )
    except Exception as exc:
        logger.error("[api/recon] trigger_reconciliation failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Reconciliation run failed. Check server logs.",
        )
