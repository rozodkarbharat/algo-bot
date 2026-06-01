"""
Pydantic v2 schemas for the Monitoring & Health Platform API.

These are HTTP response models only — NOT Beanie documents.
Used by app/routes/v1/health.py endpoints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ── Heartbeat schemas ─────────────────────────────────────────────────────────

class HeartbeatResponse(BaseModel):
    """
    Status of a single component's heartbeat.

    ``age_seconds`` reflects how long ago the last heartbeat was recorded.
    ``is_stale`` is True when age_seconds exceeds stale_threshold_seconds.
    """

    component_name: str = Field(..., description="Unique component identifier")
    last_seen: Optional[datetime] = Field(
        default=None,
        description="UTC timestamp of the most recent heartbeat, or None if never seen",
    )
    age_seconds: float = Field(
        ...,
        ge=0.0,
        description="Seconds elapsed since the last heartbeat (0.0 if never seen)",
    )
    is_stale: bool = Field(
        ...,
        description="True when age_seconds > stale_threshold_seconds",
    )
    stale_threshold_seconds: int = Field(
        ...,
        gt=0,
        description="Number of seconds without a heartbeat before the component is deemed stale",
    )


class HeartbeatReportResponse(BaseModel):
    """
    Full snapshot of the in-memory heartbeat registry.

    Components are partitioned into three mutually exclusive lists:
    - ``alive``      — heartbeated recently (within threshold)
    - ``stale``      — last heartbeat exceeds the staleness threshold
    - ``never_seen`` — registered but no heartbeat has been recorded yet
    """

    alive: list[str] = Field(
        default_factory=list,
        description="Component names that are heartbeating on schedule",
    )
    stale: list[str] = Field(
        default_factory=list,
        description="Component names whose last heartbeat is older than the stale threshold",
    )
    never_seen: list[str] = Field(
        default_factory=list,
        description="Registered components that have not yet sent a heartbeat",
    )
    heartbeats: list[HeartbeatResponse] = Field(
        default_factory=list,
        description="Per-component heartbeat detail for all registered components",
    )
    generated_at: datetime = Field(
        ...,
        description="UTC timestamp when this report was generated",
    )
    all_healthy: bool = Field(
        ...,
        description="True when stale and never_seen are both empty",
    )


# ── Health summary schema ─────────────────────────────────────────────────────

class HealthSummaryResponse(BaseModel):
    """
    Lightweight health summary suitable for dashboards and status pages.

    Reads from the DB cache rather than running live checks, making it
    fast and suitable for high-frequency polling. Use GET /api/v1/health
    for a live check that may be slower.
    """

    overall_status: str = Field(
        ...,
        description="Aggregated platform status: 'healthy' | 'degraded' | 'unhealthy'",
    )
    total_components: int = Field(
        ...,
        ge=0,
        description="Total number of monitored components",
    )
    healthy_count: int = Field(
        ...,
        ge=0,
        description="Components currently in healthy state",
    )
    degraded_count: int = Field(
        ...,
        ge=0,
        description="Components in degraded state (functioning with warnings)",
    )
    unhealthy_count: int = Field(
        ...,
        ge=0,
        description="Components in unhealthy state (failing checks, action required)",
    )
    open_incidents: int = Field(
        ...,
        ge=0,
        description="Total open incidents (OPEN | ACKNOWLEDGED | INVESTIGATING)",
    )
    critical_incidents: int = Field(
        ...,
        ge=0,
        description="Open incidents with CRITICAL severity",
    )
    last_check_at: Optional[datetime] = Field(
        default=None,
        description="UTC timestamp of the most recently updated component health record",
    )
    uptime_pct: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Percentage of monitored components currently in healthy state",
    )
    component_map: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of component_name to its current status string",
    )
