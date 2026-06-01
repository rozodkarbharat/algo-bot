"""
AlertEvent Beanie document.

Persistent record of every notification dispatched by the alert service.
Allows the dashboard to display alert history and deduplicate rapid bursts.
"""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Optional

from beanie import Document
from pydantic import Field
from pymongo import IndexModel, ASCENDING, DESCENDING


class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertChannel(StrEnum):
    EMAIL = "email"
    TELEGRAM = "telegram"
    SYSTEM = "system"   # internal log only, no external dispatch


class AlertEvent(Document):
    event_type: str = Field(
        ...,
        description=(
            "Machine-readable event type: signal_generated, order_executed, "
            "sl_hit, broker_disconnected, daily_loss_limit, scheduler_failure, system_error"
        ),
    )
    severity: AlertSeverity = Field(default=AlertSeverity.INFO)
    title: str = Field(..., description="Short human-readable title")
    body: str = Field(..., description="Full alert message body")
    payload: Optional[dict[str, Any]] = Field(default=None, description="Structured event data")

    # Delivery tracking
    channel: AlertChannel = Field(default=AlertChannel.SYSTEM)
    delivered: bool = Field(default=False)
    delivered_at: Optional[datetime] = Field(default=None)
    delivery_error: Optional[str] = Field(default=None)
    retry_count: int = Field(default=0)

    # Dedup key: same event_type + same trading_date → skip if already sent within cooldown
    dedup_key: Optional[str] = Field(default=None, description="Dedup key for burst suppression")

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "alert_events"
        indexes = [
            IndexModel([("timestamp", DESCENDING)]),
            IndexModel([("event_type", ASCENDING), ("timestamp", DESCENDING)]),
            IndexModel([("dedup_key", ASCENDING), ("timestamp", DESCENDING)]),
            IndexModel([("delivered", ASCENDING)]),
        ]
