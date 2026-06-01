"""
Authentication API routes.

POST /api/v1/auth/login          — exchange credentials for token pair
POST /api/v1/auth/logout         — revoke refresh token, clear session
POST /api/v1/auth/refresh        — rotate refresh token, issue new access token
GET  /api/v1/auth/me             — return current user profile
POST /api/v1/auth/change-password — update own password (revokes all refresh tokens)
POST /api/v1/auth/users          — create a new user (admin only)
GET  /api/v1/auth/users          — list all users (admin only)
DELETE /api/v1/auth/users/{username} — deactivate user (admin only)
GET  /api/v1/auth/audit-logs     — paginated audit trail (admin only)

Auth routes are intentionally NOT protected by Depends(get_current_user) at the
router level — they must remain public so clients can log in.
The /me, /change-password, /users, and /audit-logs routes use their own guards.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Body, Depends, Query, Request

from app.config.settings import settings
from app.core.exceptions import ValidationException
from app.middleware.auth_middleware import get_current_user, require_admin
from app.models.audit_log import AuditLog
from app.models.user import User, UserRole
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.user_repository import UserRepository
from app.schemas.auth import (
    AuditLogResponse,
    ChangePasswordRequest,
    CreateUserRequest,
    LoginRequest,
    LogoutRequest,
    LogoutResponse,
    RefreshRequest,
    RefreshTokenResponse,
    TokenResponse,
    UserResponse,
)
from app.schemas.common import PaginatedResponse
from app.services import auth_service as _auth
from app.services.audit_service import audit_service
from app.utils.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)
_user_repo = UserRepository()
_audit_repo = AuditLogRepository()


# ── Login ─────────────────────────────────────────────────────────────────────

@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login and receive access + refresh tokens",
)
async def login(body: LoginRequest, request: Request) -> TokenResponse:
    access, refresh, user = await _auth.login(body.username, body.password)

    await audit_service.log(
        action="login",
        resource="auth",
        user=user,
        request=request,
    )

    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=_auth.user_to_response(user),
    )


# ── Logout ────────────────────────────────────────────────────────────────────

@router.post(
    "/logout",
    response_model=LogoutResponse,
    summary="Logout — revokes the supplied refresh token",
)
async def logout(
    request: Request,
    body: LogoutRequest = Body(default_factory=LogoutRequest),
    current_user: User = Depends(get_current_user),
) -> LogoutResponse:
    """
    Server-side logout:
      - Revokes the refresh token (if supplied) so it cannot be rotated again.
      - Access tokens remain valid until their 30-min TTL expires; this is
        acceptable because access tokens are short-lived and stateless.
      - For immediate access-token invalidation in future, add a Redis denylist.
    """
    await _auth.logout(body.refresh_token, current_user)

    await audit_service.log(
        action="logout",
        resource="auth",
        user=current_user,
        request=request,
    )
    return LogoutResponse()


# ── Refresh ───────────────────────────────────────────────────────────────────

@router.post(
    "/refresh",
    response_model=RefreshTokenResponse,
    summary="Rotate refresh token and receive a new access + refresh token pair",
)
async def refresh(body: RefreshRequest) -> RefreshTokenResponse:
    """
    Token rotation:
      1. Validates the supplied refresh token against the DB.
      2. Revokes (consumes) it atomically.
      3. Issues a fresh access token and a fresh refresh token.

    If a revoked token is re-presented (replay attack), all refresh tokens
    for the affected user are revoked and they must log in again.
    """
    new_access, new_refresh, _user = await _auth.refresh_access_token(body.refresh_token)
    return RefreshTokenResponse(
        access_token=new_access,
        refresh_token=new_refresh,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


# ── Current user ──────────────────────────────────────────────────────────────

@router.get(
    "/me",
    response_model=UserResponse,
    summary="Return the current authenticated user",
)
async def me(current_user: User = Depends(get_current_user)) -> UserResponse:
    return _auth.user_to_response(current_user)


# ── Change password ───────────────────────────────────────────────────────────

@router.post(
    "/change-password",
    response_model=UserResponse,
    summary="Change own password (revokes all active refresh tokens)",
)
async def change_password(
    body: ChangePasswordRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> UserResponse:
    await _auth.change_password(current_user, body.current_password, body.new_password)
    await audit_service.log(
        action="password_changed",
        resource="auth",
        user=current_user,
        request=request,
    )
    return _auth.user_to_response(current_user)


# ── User management (admin only) ──────────────────────────────────────────────

@router.post(
    "/users",
    response_model=UserResponse,
    status_code=201,
    summary="Create a new user (admin only)",
)
async def create_user(
    body: CreateUserRequest,
    request: Request,
    _admin: User = Depends(require_admin),
) -> UserResponse:
    user = await _auth.create_user(
        username=body.username,
        email=body.email,
        password=body.password,
        role=body.role,
    )
    await audit_service.log(
        action="user_created",
        resource="users",
        resource_id=str(user.id),
        user=_admin,
        detail={"username": body.username, "role": body.role},
        request=request,
    )
    return _auth.user_to_response(user)


@router.get(
    "/users",
    response_model=PaginatedResponse[UserResponse],
    summary="List all users (admin only)",
)
async def list_users(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _admin: User = Depends(require_admin),
) -> PaginatedResponse[UserResponse]:
    users = await _user_repo.get_active_users()
    total = len(users)
    skip = (page - 1) * page_size
    items = [_auth.user_to_response(u) for u in users[skip : skip + page_size]]
    return PaginatedResponse.build(items=items, total=total, page=page, page_size=page_size)


@router.delete(
    "/users/{username}",
    response_model=UserResponse,
    summary="Deactivate a user account (admin only)",
)
async def deactivate_user(
    username: str,
    request: Request,
    admin: User = Depends(require_admin),
) -> UserResponse:
    if username == admin.username:
        raise ValidationException("Cannot deactivate your own account.")
    user = await _user_repo.get_by_username(username)
    if not user:
        from app.core.exceptions import DocumentNotFoundException
        raise DocumentNotFoundException("User", username)
    user.is_active = False
    user.updated_at = datetime.now(timezone.utc)
    await user.save()
    await audit_service.log(
        action="user_deactivated",
        resource="users",
        resource_id=str(user.id),
        user=admin,
        detail={"username": username},
        request=request,
    )
    return _auth.user_to_response(user)


# ── Audit log query (admin only) ──────────────────────────────────────────────

@router.get(
    "/audit-logs",
    response_model=PaginatedResponse[AuditLogResponse],
    summary="Query the audit trail (admin only)",
)
async def get_audit_logs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    user_id: Optional[str] = Query(default=None, description="Filter by user ID"),
    action: Optional[str] = Query(default=None, description="Filter by action name"),
    _admin: User = Depends(require_admin),
) -> PaginatedResponse[AuditLogResponse]:
    skip = (page - 1) * page_size
    logs = await _audit_repo.get_recent(
        limit=page_size, skip=skip, user_id=user_id, action=action
    )
    total = await _audit_repo.count_query(user_id=user_id, action=action)
    items = [
        AuditLogResponse(
            id=str(log.id),
            user_id=log.user_id,
            username=log.username,
            action=log.action,
            resource=log.resource,
            resource_id=log.resource_id,
            detail=log.detail,
            ip_address=log.ip_address,
            request_id=log.request_id,
            timestamp=log.timestamp,
            success=log.success,
            error_message=log.error_message,
        )
        for log in logs
    ]
    return PaginatedResponse.build(items=items, total=total, page=page, page_size=page_size)
