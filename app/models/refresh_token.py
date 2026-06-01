"""
RefreshToken Beanie document.

Backs stateful refresh token rotation:
  - Each issued refresh token is stored with a unique jti (JWT ID).
  - On /refresh: old token is revoked, new token issued.
  - On /logout: token is revoked immediately.
  - MongoDB TTL index auto-deletes expired documents.

Security properties:
  - Refresh token reuse detection: a revoked token presented again triggers
    revocation of all tokens for that user (token family theft detection).
  - Access tokens remain stateless (short-lived, no DB lookup on every request).
"""

from datetime import datetime, timezone
from typing import Optional

from beanie import Document
from pydantic import Field
from pymongo import IndexModel, ASCENDING, DESCENDING


class RefreshToken(Document):
    jti: str = Field(..., description="Unique JWT ID embedded in the signed token")
    user_id: str = Field(..., description="str(User._id) — owner of this token")
    expires_at: datetime = Field(..., description="UTC expiry — matches the JWT exp claim")
    revoked_at: Optional[datetime] = Field(
        default=None, description="Set when the token is consumed or explicitly revoked"
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_valid(self) -> bool:
        return self.revoked_at is None and self.expires_at > datetime.now(timezone.utc)

    class Settings:
        name = "refresh_tokens"
        indexes = [
            IndexModel([("jti", ASCENDING)], unique=True),
            IndexModel([("user_id", ASCENDING)]),
            # TTL index: MongoDB auto-deletes expired token documents.
            # expireAfterSeconds=0 means delete at the expires_at timestamp.
            IndexModel([("expires_at", ASCENDING)], expireAfterSeconds=0),
        ]
