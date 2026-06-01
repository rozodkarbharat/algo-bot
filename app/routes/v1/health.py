"""
Monitoring & Health Platform API routes.

All endpoints operate under the /api/v1/health prefix (mounted in __init__.py).

GET /api/v1/health              — run all checks live, return AggregateHealthResponse
GET /api/v1/health/components   — per-component statuses from DB cache
GET /api/v1/health/incidents    — active/recent incidents (filterable)
GET /api/v1/health/summary      — lightweight dashboard summary (DB-cached)
GET /api/v1/health/heartbeats   — heartbeat tracker report for all registered components

Design notes:
- /health (live) runs all 7 component checks concurrently; typical latency 100–500 ms.
- /health/summary and /health/components read from the DB cache, making them suitable
  for high-frequency dashboard polling without hammering downstream services.
- Lazy repository/monitor imports inside handlers prevent circular import chains
  at module load time.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.monitoring.health_aggregator import health_aggregator
from app.monitoring.incident_manager import incident_manager
from app.monitoring.heartbeat import heartbeat_tracker, DEFAULT_STALE_THRESHOLD_SECONDS
from app.models.system_incident import IncidentStatus
from app.models.alert_event import AlertSeverity
from app.schemas.ops import (
    AggregateHealthResponse,
    ComponentHealthResponse,
    IncidentListResponse,
    IncidentResponse,
)
from app.schemas.health import (
    HealthSummaryResponse,
    HeartbeatReportResponse,
    HeartbeatResponse,
)
from app.utils.logger import get_logger
from app.utils.market_time import now_utc

logger = get_logger(__name__)

router = APIRouter()


# ── Live aggregate health check ───────────────────────────────────────────────

@router.get(
    "",
    response_model=AggregateHealthResponse,
    summary="Live platform health check",
    description=(
        "Runs all registered component health checks concurrently and returns the "
        "aggregate result. Also persists the results to the DB and updates incident "
        "lifecycle for any failures. "
        "Typical latency: 100–500 ms depending on broker API response time. "
        "For a cheaper cached read, use GET /api/v1/health/summary instead."
    ),
)
async def get_aggregate_health() -> AggregateHealthResponse:
    """Run all health checks live and return the aggregate result."""
    try:
        report = await health_aggregator.run_all()
    except Exception as exc:
        logger.error("[health] run_all failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Health aggregator failed to complete checks.",
        ) from exc

    components = [
        ComponentHealthResponse(
            component_name=c.component_name,
            status=c.status,
            last_heartbeat=c.checked_at if c.healthy else None,
            latency_ms=c.latency_ms,
            error_count=c.error_count,
            error_message=c.error_message,
            metadata=c.metadata,
            updated_at=c.checked_at,
        )
        for c in report.components
    ]
    return AggregateHealthResponse(
        overall_status=report.overall_status,
        components=components,
        open_incident_count=report.open_incident_count,
        healthy_count=report.healthy_count,
        degraded_count=report.degraded_count,
        unhealthy_count=report.unhealthy_count,
        generated_at=report.generated_at,
    )


# ── DB-cached component statuses ──────────────────────────────────────────────

@router.get(
    "/components",
    response_model=list[ComponentHealthResponse],
    summary="Per-component health from DB cache",
    description=(
        "Returns the last-persisted health state for each component without "
        "re-running checks. The scheduler refreshes this cache every 60 seconds. "
        "Use this endpoint for dashboard polling to avoid hammering downstream services."
    ),
)
async def get_component_statuses() -> list[ComponentHealthResponse]:
    """Return cached per-component health statuses from the database."""
    try:
        from app.repositories.system_health_status_repository import SystemHealthStatusRepository
        statuses = await SystemHealthStatusRepository().get_all()
    except Exception as exc:
        logger.error("[health/components] repository error: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Could not retrieve component health statuses from database.",
        ) from exc

    return [
        ComponentHealthResponse(
            component_name=s.component_name,
            status=s.status.value,
            last_heartbeat=s.last_heartbeat,
            latency_ms=s.latency_ms,
            error_count=s.error_count,
            error_message=s.error_message,
            metadata=s.metadata,
            updated_at=s.updated_at,
        )
        for s in statuses
    ]


# ── Incidents ─────────────────────────────────────────────────────────────────

@router.get(
    "/incidents",
    response_model=IncidentListResponse,
    summary="List operational incidents",
    description=(
        "Returns recent incidents. Use ``open_only=true`` to see only active "
        "(OPEN | ACKNOWLEDGED | INVESTIGATING) incidents. "
        "Optionally filter by a specific component name."
    ),
)
async def list_health_incidents(
    component: Optional[str] = Query(
        default=None,
        description="Filter results to a specific component name (e.g. 'mongodb')",
    ),
    open_only: bool = Query(
        default=False,
        description="Return only OPEN / ACKNOWLEDGED / INVESTIGATING incidents",
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=200,
        description="Maximum number of incidents to return (ignored when open_only=true)",
    ),
) -> IncidentListResponse:
    """Return incidents, optionally filtered by component or open status."""
    try:
        if open_only:
            items = await incident_manager.list_open(component)
        else:
            items = await incident_manager.list_recent(limit)
            if component:
                items = [i for i in items if i.component == component]
    except Exception as exc:
        logger.error("[health/incidents] incident_manager error: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Could not retrieve incidents.",
        ) from exc

    open_count = sum(
        1 for i in items
        if i.status in (
            IncidentStatus.OPEN,
            IncidentStatus.ACKNOWLEDGED,
            IncidentStatus.INVESTIGATING,
        )
    )
    responses = [
        IncidentResponse(
            incident_id=i.incident_id,
            severity=i.severity,
            component=i.component,
            description=i.description,
            detected_at=i.detected_at,
            resolved_at=i.resolved_at,
            status=i.status,
            timeline=i.timeline,
            metadata=i.metadata,
            created_at=i.created_at,
        )
        for i in items
    ]
    return IncidentListResponse(items=responses, total=len(responses), open_count=open_count)


# ── Lightweight summary ───────────────────────────────────────────────────────

@router.get(
    "/summary",
    response_model=HealthSummaryResponse,
    summary="Lightweight health summary for dashboards",
    description=(
        "Reads cached component statuses from the DB and open incidents from MongoDB "
        "to produce a fast, low-overhead summary suitable for high-frequency polling. "
        "Does NOT run live health checks. The scheduler refreshes underlying data "
        "every 60 seconds. "
        "``uptime_pct`` = healthy_count / total_components × 100 (0 when no components "
        "are registered)."
    ),
)
async def get_health_summary() -> HealthSummaryResponse:
    """Build a lightweight health summary from DB-cached data."""
    # ── 1. Fetch cached component statuses ────────────────────────────────────
    try:
        from app.repositories.system_health_status_repository import SystemHealthStatusRepository
        statuses = await SystemHealthStatusRepository().get_all()
    except Exception as exc:
        logger.error("[health/summary] component status fetch failed: %s", exc)
        statuses = []

    total_components = len(statuses)
    healthy_count = sum(1 for s in statuses if s.status.value == "healthy")
    degraded_count = sum(1 for s in statuses if s.status.value == "degraded")
    unhealthy_count = sum(1 for s in statuses if s.status.value == "unhealthy")

    # Derive overall_status from component breakdown
    if unhealthy_count > 0:
        overall_status = "unhealthy"
    elif degraded_count > 0:
        overall_status = "degraded"
    else:
        overall_status = "healthy"

    # Most recently updated component's timestamp
    last_check_at: Optional[datetime] = None
    if statuses:
        last_check_at = max((s.updated_at for s in statuses), default=None)

    uptime_pct = (healthy_count / total_components * 100.0) if total_components > 0 else 0.0
    component_map: dict[str, str] = {s.component_name: s.status.value for s in statuses}

    # ── 2. Fetch open incidents ───────────────────────────────────────────────
    try:
        open_incident_list = await incident_manager.list_open()
    except Exception as exc:
        logger.error("[health/summary] incident fetch failed: %s", exc)
        open_incident_list = []

    open_incidents = len(open_incident_list)
    critical_incidents = sum(
        1 for i in open_incident_list
        if i.severity == AlertSeverity.CRITICAL
    )

    return HealthSummaryResponse(
        overall_status=overall_status,
        total_components=total_components,
        healthy_count=healthy_count,
        degraded_count=degraded_count,
        unhealthy_count=unhealthy_count,
        open_incidents=open_incidents,
        critical_incidents=critical_incidents,
        last_check_at=last_check_at,
        uptime_pct=round(uptime_pct, 2),
        component_map=component_map,
    )


# ── Heartbeat tracker report ──────────────────────────────────────────────────

@router.get(
    "/heartbeats",
    response_model=HeartbeatReportResponse,
    summary="Heartbeat tracker report",
    description=(
        "Returns the in-memory heartbeat registry snapshot for all registered "
        "components. Components are partitioned into three lists: "
        "``alive`` (within threshold), ``stale`` (overdue heartbeat), and "
        "``never_seen`` (registered but never heartbeated). "
        "``all_healthy`` is True only when both stale and never_seen are empty."
    ),
)
async def get_heartbeat_report() -> HeartbeatReportResponse:
    """Return the heartbeat registry snapshot from the in-memory tracker."""
    try:
        report = await heartbeat_tracker.report()
    except Exception as exc:
        logger.error("[health/heartbeats] heartbeat_tracker.report() failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Could not retrieve heartbeat report.",
        ) from exc

    # Build per-component HeartbeatResponse entries for all known components.
    # Components in never_seen have no record so we synthesise a zero-age entry.
    heartbeat_entries: list[HeartbeatResponse] = []

    for name in sorted(report.alive + report.stale + report.never_seen):
        rec = report.records.get(name)
        if rec is not None:
            heartbeat_entries.append(
                HeartbeatResponse(
                    component_name=name,
                    last_seen=rec.last_seen,
                    age_seconds=round(rec.age_seconds, 2),
                    is_stale=rec.is_stale,
                    stale_threshold_seconds=rec.stale_threshold_seconds,
                )
            )
        else:
            # Component registered but never heartbeated
            heartbeat_entries.append(
                HeartbeatResponse(
                    component_name=name,
                    last_seen=None,
                    age_seconds=0.0,
                    is_stale=False,
                    stale_threshold_seconds=DEFAULT_STALE_THRESHOLD_SECONDS,
                )
            )

    return HeartbeatReportResponse(
        alive=report.alive,
        stale=report.stale,
        never_seen=report.never_seen,
        heartbeats=heartbeat_entries,
        generated_at=report.generated_at,
        all_healthy=report.all_healthy,
    )
