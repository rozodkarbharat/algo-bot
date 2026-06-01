"""
User Beanie document.

Stores dashboard operator accounts with role-based access control.
Roles:
  - admin  : full access — can change trading mode, trigger syncs, manage users
  - trader : operational access — can start/stop engine, view all data, no user management
  - viewer : read-only — can view dashboard, no control actions
"""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Optional

from beanie import Document
from pydantic import Field, EmailStr
from pymongo import IndexModel, ASCENDING


class UserRole(StrEnum):
    ADMIN = "admin"
    TRADER = "trader"
    VIEWER = "viewer"


class User(Document):
    username: str = Field(..., description="Unique login username")
    email: str = Field(..., description="User email address")
    hashed_password: str = Field(..., description="bcrypt-hashed password")
    role: UserRole = Field(default=UserRole.VIEWER, description="Access role")
    is_active: bool = Field(default=True, description="False = soft-deleted / suspended")
    last_login: Optional[datetime] = Field(default=None)
    password_changed_at: Optional[datetime] = Field(default=None)
    # Account lockout — reset on successful login
    failed_login_attempts: int = Field(default=0, description="Consecutive failed login count")
    locked_until: Optional[datetime] = Field(
        default=None, description="UTC time until which the account is locked; None = not locked"
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "users"
        indexes = [
            IndexModel([("username", ASCENDING)], unique=True),
            IndexModel([("email", ASCENDING)], unique=True),
        ]
