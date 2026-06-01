"""
Auth API schemas — request/response models for authentication endpoints.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models.user import UserRole


# ── Request models ────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    # Client should send the refresh token so it can be revoked immediately.
    # Optional for backward compatibility with clients that only discard tokens client-side.
    refresh_token: Optional[str] = Field(
        default=None, description="Refresh token to revoke on the server"
    )


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8, max_length=128)


class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    email: str = Field(..., max_length=256)
    password: str = Field(..., min_length=8, max_length=128)
    role: UserRole = Field(default=UserRole.VIEWER)


# ── Response models ───────────────────────────────────────────────────────────

class UserResponse(BaseModel):
    id: str
    username: str
    email: str
    role: UserRole
    is_active: bool
    last_login: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="Access token lifetime in seconds")
    user: UserResponse


class RefreshTokenResponse(BaseModel):
    access_token: str
    refresh_token: str = Field(..., description="New refresh token (old one is revoked)")
    token_type: str = "bearer"
    expires_in: int


class LogoutResponse(BaseModel):
    message: str = "Logged out successfully"


# ── Audit log query response ──────────────────────────────────────────────────

class AuditLogResponse(BaseModel):
    id: str
    user_id: Optional[str]
    username: str
    action: str
    resource: str
    resource_id: Optional[str]
    detail: Optional[dict]
    ip_address: Optional[str]
    request_id: Optional[str]
    timestamp: datetime
    success: bool
    error_message: Optional[str]

    model_config = {"from_attributes": True}
