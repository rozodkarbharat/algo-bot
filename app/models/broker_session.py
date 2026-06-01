"""
Broker session document — persistent record of an authenticated broker session.

Persistence contract:
  - One row per `broker_name` (e.g. "AngelOne").
  - Stores the JWT access token, refresh token, feed token, and expiry
    so that the application can warm-start a session across deploys
    instead of re-logging on every cold start.
  - The in-memory `AngelOneAuth` cache remains the runtime source of
    truth; this document is a *durable copy* used by the session-refresh
    scheduler job and post-deploy bootstrap.

Security note:
  - Tokens are persisted in clear text inside the application database.
    This is acceptable for a single-tenant trading bot whose database is
    already access-restricted; for multi-tenant deployments wrap tokens
    in a KMS/secret store before persisting.
"""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Optional

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, IndexModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BrokerSessionStatus(StrEnum):
    """Lifecycle of a broker session row."""

    ACTIVE = "ACTIVE"     # tokens fresh, ready to use
    EXPIRED = "EXPIRED"   # last known token expired; awaiting refresh
    REVOKED = "REVOKED"   # session explicitly invalidated (logout / kill switch)
    FAILED = "FAILED"     # last login/refresh attempt failed


class BrokerSession(Document):
    """
    Persisted broker authentication session.

    Collection: broker_sessions
    Unique constraint: broker_name
    """

    broker_name: str = Field(..., description="e.g. 'AngelOne'")

    access_token: Optional[str] = Field(default=None, description="JWT bearer token")
    refresh_token: Optional[str] = Field(default=None, description="Long-lived refresh token")
    feed_token: Optional[str] = Field(
        default=None, description="WebSocket market-feed token (Angel One)"
    )

    session_status: BrokerSessionStatus = Field(
        default=BrokerSessionStatus.ACTIVE,
        description="Most recent known status of this session",
    )

    expires_at: Optional[datetime] = Field(
        default=None, description="UTC datetime at which the access_token expires"
    )
    last_refresh_at: Optional[datetime] = Field(
        default=None, description="UTC timestamp of the last successful login/refresh"
    )
    last_error_at: Optional[datetime] = Field(default=None)
    last_error: Optional[str] = Field(default=None, description="Most recent error message")

    metadata: dict = Field(default_factory=dict)

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "broker_sessions"
        indexes = [
            IndexModel([("broker_name", ASCENDING)], unique=True, name="broker_name_unique"),
            IndexModel([("session_status", ASCENDING)]),
        ]

    def mark_updated(self) -> None:
        self.updated_at = _utcnow()
