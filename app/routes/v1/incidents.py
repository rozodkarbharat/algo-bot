"""
Incident management API — standalone lifecycle routes.

Complements the read-only /ops/incidents endpoint with explicit lifecycle
actions (acknowledge, resolve) and a cleaner top-level URL.

GET  /api/v1/incidents              — list incidents (filterable)
GET  /api/v1/incidents/{id}         — single incident detail
POST /api/v1/incidents/{id}/acknowledge — mark as ACKNOWLEDGED
POST /api/v1/incidents/{id}/resolve     — mark as RESOLVED
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel

from app.monitoring.incident_manager import incident_manager
from app.models.system_incident import IncidentStatus
from app.schemas.common import MessageResponse
from app.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()


# ── Response schemas ──────────────────────────────────────────────────────────

class IncidentResponse(BaseModel):
    incident_id: str
    severity: str
    component: str
    description: str
    status: str
    detected_at: str
    resolved_at: Optional[str]
    timeline: list[dict]
    metadata: dict

    @classmethod
    def from_doc(cls, doc) -> "IncidentResponse":
        return cls(
            incident_id=doc.incident_id,
            severity=doc.severity,
            component=doc.component,
            description=doc.description,
            status=doc.status,
            detected_at=doc.detected_at.isoformat(),
            resolved_at=doc.resolved_at.isoformat() if doc.resolved_at else None,
            timeline=doc.timeline,
            metadata=doc.metadata,
        )


class IncidentListResponse(BaseModel):
    items: list[IncidentResponse]
    total: int
    open_count: int


class AcknowledgeRequest(BaseModel):
    message: str = "Acknowledged by operator."


class ResolveRequest(BaseModel):
    message: str = "Resolved."


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=IncidentListResponse,
    summary="List incidents",
    description=(
        "Returns recent incidents. Use `open_only=true` to see only active incidents, "
        "or `component=<name>` to filter by affected component."
    ),
)
async def list_incidents(
    component: Optional[str] = Query(default=None, description="Filter by component name"),
    open_only: bool = Query(default=False, description="Return only OPEN/ACKNOWLEDGED/INVESTIGATING incidents"),
    limit: int = Query(default=50, ge=1, le=200),
) -> IncidentListResponse:
    if open_only:
        items = await incident_manager.list_open(component)
    else:
        items = await incident_manager.list_recent(limit)
        if component:
            items = [i for i in items if i.component == component]

    open_count = sum(
        1 for i in items
        if i.status in (
            IncidentStatus.OPEN,
            IncidentStatus.ACKNOWLEDGED,
            IncidentStatus.INVESTIGATING,
        )
    )
    return IncidentListResponse(
        items=[IncidentResponse.from_doc(i) for i in items],
        total=len(items),
        open_count=open_count,
    )


@router.get(
    "/{incident_id}",
    response_model=IncidentResponse,
    summary="Get a single incident by ID",
)
async def get_incident(incident_id: str) -> IncidentResponse:
    # Use the internal helper via public list to avoid exposing _get
    items = await incident_manager.list_recent(limit=500)
    match = next((i for i in items if i.incident_id == incident_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Incident not found: {incident_id}")
    return IncidentResponse.from_doc(match)


@router.post(
    "/{incident_id}/acknowledge",
    response_model=MessageResponse,
    summary="Acknowledge an incident",
    description=(
        "Transitions the incident to ACKNOWLEDGED, indicating that an operator "
        "is aware and working on it. Adds a timeline entry with the supplied message."
    ),
)
async def acknowledge_incident(
    incident_id: str,
    body: AcknowledgeRequest = Body(default_factory=AcknowledgeRequest),
) -> MessageResponse:
    result = await incident_manager.acknowledge(incident_id, body.message)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Incident not found: {incident_id}")
    return MessageResponse(
        message=f"Incident {incident_id} acknowledged.",
        success=True,
    )


@router.post(
    "/{incident_id}/resolve",
    response_model=MessageResponse,
    summary="Resolve an incident",
    description=(
        "Marks the incident RESOLVED and records the resolution time. "
        "Resolution time = resolved_at - detected_at."
    ),
)
async def resolve_incident(
    incident_id: str,
    body: ResolveRequest = Body(default_factory=ResolveRequest),
) -> MessageResponse:
    result = await incident_manager.resolve(incident_id, body.message)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Incident not found: {incident_id}")

    resolution_time: Optional[str] = None
    if result.resolved_at and result.detected_at:
        delta = result.resolved_at - result.detected_at
        total_seconds = int(delta.total_seconds())
        h, rem = divmod(total_seconds, 3600)
        m, s = divmod(rem, 60)
        resolution_time = f"{h}h {m}m {s}s" if h else f"{m}m {s}s"

    msg = f"Incident {incident_id} resolved."
    if resolution_time:
        msg += f" Resolution time: {resolution_time}."
    return MessageResponse(message=msg, success=True)
