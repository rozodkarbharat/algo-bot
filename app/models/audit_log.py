"""
AuditLog Beanie document.

Immutable record of every user action that changes system state.
Appended by the audit service — never updated or deleted in production.
"""

from datetime import datetime, timezone
from typing import Any, Optional

from beanie import Document
from pydantic import Field
from pymongo import IndexModel, ASCENDING, DESCENDING


class AuditLog(Document):
    # Who
    user_id: Optional[str] = Field(default=None, description="User._id as string (None = system)")
    username: str = Field(default="system", description="Username at time of action")

    # What
    action: str = Field(..., description="Short action identifier, e.g. 'trading_mode_changed'")
    resource: str = Field(..., description="Affected resource, e.g. 'settings', 'paper_position'")
    resource_id: Optional[str] = Field(default=None, description="Affected document ID if applicable")
    detail: Optional[dict[str, Any]] = Field(default=None, description="Free-form context payload")

    # Where / when
    ip_address: Optional[str] = Field(default=None, description="Client IP address")
    request_id: Optional[str] = Field(default=None, description="X-Request-ID from logging middleware")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of the action",
    )

    # Outcome
    success: bool = Field(default=True)
    error_message: Optional[str] = Field(default=None)

    class Settings:
        name = "audit_logs"
        indexes = [
            IndexModel([("timestamp", DESCENDING)]),
            IndexModel([("user_id", ASCENDING), ("timestamp", DESCENDING)]),
            IndexModel([("action", ASCENDING), ("timestamp", DESCENDING)]),
            IndexModel([("resource", ASCENDING), ("resource_id", ASCENDING)]),
        ]
