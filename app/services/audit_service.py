"""
Audit service — fire-and-forget audit trail.

Usage in routes:
    await audit_service.log(
        action="trading_mode_changed",
        resource="settings",
        user=current_user,
        detail={"from": "paper", "to": "live"},
        request=request,  # FastAPI Request for IP + request_id
    )

All writes are best-effort: errors are logged but never re-raised so a
failed audit write never kills the user's request.
"""

import asyncio
from typing import Any, Optional

from fastapi import Request

from app.models.audit_log import AuditLog
from app.models.user import User
from app.repositories.audit_log_repository import AuditLogRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)

_repo = AuditLogRepository()


class AuditService:

    async def log(
        self,
        action: str,
        resource: str,
        *,
        user: Optional[User] = None,
        resource_id: Optional[str] = None,
        detail: Optional[dict[str, Any]] = None,
        request: Optional[Request] = None,
        success: bool = True,
        error_message: Optional[str] = None,
    ) -> None:
        """Write an audit record. Never raises."""
        try:
            ip: Optional[str] = None
            req_id: Optional[str] = None

            if request:
                # FastAPI Request: client may be behind a proxy
                forwarded = request.headers.get("x-forwarded-for")
                ip = forwarded.split(",")[0].strip() if forwarded else (
                    request.client.host if request.client else None
                )
                req_id = request.headers.get("x-request-id")

            entry = AuditLog(
                user_id=str(user.id) if user else None,
                username=user.username if user else "system",
                action=action,
                resource=resource,
                resource_id=resource_id,
                detail=detail,
                ip_address=ip,
                request_id=req_id,
                success=success,
                error_message=error_message,
            )
            await entry.insert()
        except Exception as exc:
            logger.error("Failed to write audit log [%s/%s]: %s", action, resource, exc)

    def log_sync(self, action: str, resource: str, **kwargs: Any) -> None:
        """Schedule a log write without blocking. Safe to call from sync code."""
        asyncio.create_task(self.log(action, resource, **kwargs))


audit_service = AuditService()
