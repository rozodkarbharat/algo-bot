"""
SystemIncident — tracks operational incidents through their lifecycle.

An incident is created when a health check detects a failure severe enough
to require operator attention.  It progresses: OPEN → INVESTIGATING → RESOLVED.
Critical incidents trigger immediate escalation (notification bypass).

Collection: system_incidents
Unique index: incident_id
"""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Optional
from uuid import uuid4

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from app.models.alert_event import AlertSeverity


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_incident_id() -> str:
    return uuid4().hex[:12]


class IncidentStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"   # operator has seen it and is working on it
    INVESTIGATING = "investigating"  # kept for backward compat; semantically == ACKNOWLEDGED
    RESOLVED = "resolved"


class SystemIncident(Document):
    """
    Operational incident record.

    Collection: system_incidents
    Unique index: incident_id
    """

    incident_id: str = Field(default_factory=_new_incident_id)

    severity: AlertSeverity = Field(..., description="info | warning | critical")
    component: str = Field(..., description="Affected component name")
    description: str = Field(..., description="Human-readable incident description")

    detected_at: datetime = Field(default_factory=_utcnow)
    resolved_at: Optional[datetime] = Field(default=None)

    status: IncidentStatus = Field(default=IncidentStatus.OPEN)

    # Timeline entries: list of {"at": ISO, "message": str}
    timeline: list[dict[str, Any]] = Field(default_factory=list)

    # Additional context
    metadata: dict[str, Any] = Field(default_factory=dict)

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "system_incidents"
        indexes = [
            IndexModel(
                [("incident_id", ASCENDING)],
                unique=True,
                name="incident_id_unique",
            ),
            IndexModel([("status", ASCENDING)]),
            IndexModel([("severity", ASCENDING)]),
            IndexModel([("component", ASCENDING)]),
            IndexModel([("detected_at", DESCENDING)]),
        ]

    def add_timeline_entry(self, message: str) -> None:
        self.timeline.append({"at": _utcnow().isoformat(), "message": message})
        self.updated_at = _utcnow()

    def mark_updated(self) -> None:
        self.updated_at = _utcnow()
