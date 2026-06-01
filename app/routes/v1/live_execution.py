"""
Live execution API routes (REAL broker orders).

All endpoints are mounted under `/api/v1/live` alongside the existing
live-signal routes. Endpoints in this file deal exclusively with the
real-money execution layer.

GET  /api/v1/live/orders            — paginated live order ledger
GET  /api/v1/live/orders/{order_id} — single live order detail
GET  /api/v1/live/positions         — paginated live position list
GET  /api/v1/live/pnl               — engine snapshot (kill switch + P&L)
POST /api/v1/live/pause             — pause live execution
POST /api/v1/live/resume            — resume live execution
POST /api/v1/live/close-all         — square off every open live position
POST /api/v1/live/emergency-stop    — kill switch + flatten all positions
POST /api/v1/live/kill-switch/engage
POST /api/v1/live/kill-switch/disengage
POST /api/v1/live/reconcile         — force order + position reconciliation

Routes delegate to LiveExecutionService only — no direct repository access.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.core.exceptions import LiveOrderNotFoundException
from app.models.live_position import LiveExitReason
from app.schemas.common import PaginatedResponse
from app.schemas.live_execution import (
    LiveCloseAllResponse,
    LiveControlResponse,
    LiveEmergencyStopRequest,
    LiveOrderResponse,
    LivePauseRequest,
    LivePnLResponse,
    LivePositionResponse,
)
from app.services.live_execution_service import live_execution_service
from app.utils.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)


# ── Orders ───────────────────────────────────────────────────────────────────

@router.get(
    "/orders",
    response_model=PaginatedResponse[LiveOrderResponse],
    summary="List live broker orders (newest first)",
)
async def list_orders(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> PaginatedResponse[LiveOrderResponse]:
    skip = (page - 1) * page_size
    orders = await live_execution_service.list_orders(
        limit=skip + page_size, skip=0
    )
    total = len(orders)
    paged = orders[skip : skip + page_size]
    items = [LiveOrderResponse.from_document(o) for o in paged]
    return PaginatedResponse.build(
        items=items, total=total, page=page, page_size=page_size
    )


@router.get(
    "/orders/{order_id}",
    response_model=LiveOrderResponse,
    summary="Fetch a single live order by id",
)
async def get_order(order_id: str) -> LiveOrderResponse:
    order = await live_execution_service.get_order(order_id)
    if order is None:
        raise LiveOrderNotFoundException(order_id)
    return LiveOrderResponse.from_document(order)


# ── Positions ────────────────────────────────────────────────────────────────

@router.get(
    "/positions",
    response_model=PaginatedResponse[LivePositionResponse],
    summary="List live positions (open + closed)",
)
async def list_positions(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    open_only: bool = Query(
        default=False,
        description="When true, return only currently open live positions",
    ),
) -> PaginatedResponse[LivePositionResponse]:
    if open_only:
        positions = await live_execution_service.list_open_positions()
        total = len(positions)
        skip = (page - 1) * page_size
        paged = positions[skip : skip + page_size]
    else:
        skip = (page - 1) * page_size
        positions = await live_execution_service.list_positions(
            limit=skip + page_size, skip=0
        )
        total = len(positions)
        paged = positions[skip : skip + page_size]

    items = [LivePositionResponse.from_document(p) for p in paged]
    return PaginatedResponse.build(
        items=items, total=total, page=page, page_size=page_size
    )


# ── P&L / Engine snapshot ────────────────────────────────────────────────────

@router.get(
    "/pnl",
    response_model=LivePnLResponse,
    summary="Live execution engine PnL snapshot",
)
async def get_pnl() -> LivePnLResponse:
    snap = await live_execution_service.snapshot()
    return LivePnLResponse(**snap.__dict__)


# ── Lifecycle controls ───────────────────────────────────────────────────────

@router.post(
    "/pause",
    response_model=LiveControlResponse,
    summary="Pause live execution (existing positions are NOT closed)",
)
async def pause(body: Optional[LivePauseRequest] = None) -> LiveControlResponse:
    reason = (body.reason if body is not None else None) or "manual_pause"
    snap = await live_execution_service.pause(reason=reason)
    return _control_response(snap, message="Live execution paused.")


@router.post(
    "/resume",
    response_model=LiveControlResponse,
    summary="Resume live execution",
)
async def resume() -> LiveControlResponse:
    snap = await live_execution_service.resume()
    return _control_response(snap, message="Live execution resumed.")


@router.post(
    "/close-all",
    response_model=LiveCloseAllResponse,
    summary="Force-close every open live position via broker exit orders",
)
async def close_all() -> LiveCloseAllResponse:
    result = await live_execution_service.close_all_open(
        reason=LiveExitReason.MANUAL_CLOSE
    )
    return LiveCloseAllResponse(
        closed=result.closed,
        reason=result.reason,
        message="Close-all dispatched to broker.",
    )


@router.post(
    "/emergency-stop",
    response_model=LiveCloseAllResponse,
    summary="Engage kill switch and flatten every open position",
)
async def emergency_stop(
    body: Optional[LiveEmergencyStopRequest] = None,
) -> LiveCloseAllResponse:
    reason = (
        (body.reason if body is not None else None) or "operator_emergency_stop"
    )
    snap = await live_execution_service.emergency_stop(reason=reason)
    return LiveCloseAllResponse(
        closed=int(snap.get("closed_positions", 0)),
        reason=reason,
        message="Emergency stop engaged. Kill switch active; positions flattened.",
    )


@router.post(
    "/kill-switch/engage",
    response_model=LiveControlResponse,
    summary="Engage the kill switch (block new orders; exits still allowed)",
)
async def engage_kill_switch(
    body: Optional[LivePauseRequest] = None,
) -> LiveControlResponse:
    reason = (
        (body.reason if body is not None else None) or "manual_kill_switch"
    )
    snap = await live_execution_service.engage_kill_switch(reason=reason)
    return _control_response(snap, message="Kill switch engaged.")


@router.post(
    "/kill-switch/disengage",
    response_model=LiveControlResponse,
    summary="Disengage the kill switch",
)
async def disengage_kill_switch() -> LiveControlResponse:
    snap = await live_execution_service.disengage_kill_switch()
    return _control_response(snap, message="Kill switch disengaged.")


@router.post(
    "/reconcile",
    summary="Force order + position reconciliation with the broker",
)
async def reconcile() -> dict:
    order_summary = await live_execution_service.reconcile_orders()
    diffs = await live_execution_service.reconcile_positions()
    return {
        "orders": order_summary,
        "position_diffs": [d.__dict__ for d in diffs],
    }


# ── Helpers ──────────────────────────────────────────────────────────────────

def _control_response(snap: dict, *, message: str) -> LiveControlResponse:
    return LiveControlResponse(
        enabled=bool(snap.get("enabled", False)),
        is_paused=bool(snap.get("is_paused", False)),
        pause_reason=snap.get("pause_reason"),
        kill_switch=snap.get("kill_switch", {}),
        message=message,
    )
