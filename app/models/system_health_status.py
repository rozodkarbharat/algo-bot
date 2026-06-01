"""
SystemHealthStatus — per-component health snapshot.

One document per component name. Upserted on every health-check cycle so
the dashboard always shows the most recent state per component.

Collection: system_health_statuses
Unique constraint: component_name
"""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Optional

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ComponentStatus(StrEnum):
    """Coarse health level for a monitored system component."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"     # functioning but with warnings
    UNHEALTHY = "unhealthy"   # failing checks; action required
    UNKNOWN = "unknown"       # no heartbeat received yet / first boot


class SystemHealthStatus(Document):
    """
    Latest health state for one system component.

    Collection: system_health_statuses
    Unique index: component_name
    """

    component_name: str = Field(..., description="Unique name for the component")
    status: ComponentStatus = Field(default=ComponentStatus.UNKNOWN)
    last_heartbeat: Optional[datetime] = Field(
        default=None,
        description="Timestamp of the most recent successful heartbeat",
    )
    latency_ms: float = Field(
        default=0.0,
        description="Last check round-trip latency (ms)",
    )
    error_count: int = Field(
        default=0,
        description="Consecutive error count — resets to 0 on recovery",
    )
    error_message: Optional[str] = Field(
        default=None,
        description="Most recent error message (None when healthy)",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Component-specific diagnostic data",
    )
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "system_health_statuses"
        indexes = [
            IndexModel(
                [("component_name", ASCENDING)],
                unique=True,
                name="component_name_unique",
            ),
            IndexModel([("status", ASCENDING)]),
            IndexModel([("updated_at", DESCENDING)]),
        ]

    def mark_updated(self) -> None:
        self.updated_at = _utcnow()
