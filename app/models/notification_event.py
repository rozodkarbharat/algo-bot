"""
NotificationEvent Beanie document.

Raw domain-event log — records that something happened in the system,
independent of whether a notification was dispatched or how it was
delivered.

Contrast with AlertEvent (app/models/alert_event.py):
  - NotificationEvent: "signal engine detected a breakout" (domain fact)
  - AlertEvent:        "we tried to notify someone and it was delivered" (delivery record)

Both are written together by the notification pipeline, but they serve
different query patterns:
  - Ops dashboards query AlertEvent for delivery health (retries, failures).
  - Analytics/audit queries use NotificationEvent for domain event frequency
    without caring about delivery mechanics.
"""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from beanie import Document
from pydantic import Field
from pymongo import IndexModel, ASCENDING, DESCENDING


class NotificationSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


def _new_event_id() -> str:
    return uuid4().hex


class NotificationEvent(Document):
    event_id: str = Field(
        default_factory=_new_event_id,
        description="Unique event identifier (hex UUID)",
    )
    event_type: str = Field(
        ..., description="Machine-readable event type (e.g. 'signal_generated', 'stop_loss_hit')"
    )
    severity: NotificationSeverity = Field(default=NotificationSeverity.INFO)
    source: str = Field(
        ...,
        description="Component/service that generated the event (e.g. 'signal_engine', 'paper_trading')",
    )
    message: str = Field(..., description="Human-readable event description")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured context: symbol, prices, component details, etc.",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "notification_events"
        indexes = [
            IndexModel([("event_id", ASCENDING)], unique=True),
            IndexModel([("event_type", ASCENDING), ("created_at", DESCENDING)]),
            IndexModel([("source", ASCENDING), ("created_at", DESCENDING)]),
            IndexModel([("severity", ASCENDING), ("created_at", DESCENDING)]),
            IndexModel([("created_at", DESCENDING)]),
        ]
