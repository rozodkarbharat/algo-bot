"""
System monitoring API routes.

GET /api/v1/system/health        — comprehensive system health snapshot
GET /api/v1/system/scheduler     — APScheduler job states
GET /api/v1/system/alerts        — recent alert events (paginated)
GET /api/v1/system/audit-logs    — audit trail (paginated, admin only)
GET /api/v1/system/readiness     — production readiness checklist
"""

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query

from app.config.settings import settings
from app.database.mongodb import get_database
from app.middleware.auth_middleware import get_current_user, require_admin
from app.models.user import User
from app.repositories.alert_event_repository import AlertEventRepository
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.market_data_sync_log_repository import MarketDataSyncLogRepository
from app.scheduler.scheduler import get_scheduler
from app.schemas.common import PaginatedResponse
from app.utils.logger import get_logger
from app.utils.market_time import now_ist

logger = get_logger(__name__)

router = APIRouter()
_alert_repo = AlertEventRepository()
_audit_repo = AuditLogRepository()
_sync_log_repo = MarketDataSyncLogRepository()


# ── Comprehensive health snapshot ─────────────────────────────────────────────

@router.get(
    "/health",
    summary="Comprehensive system health snapshot",
    dependencies=[Depends(get_current_user)],
)
async def system_health() -> dict[str, Any]:
    health: dict[str, Any] = {
        "timestamp": now_ist().isoformat(),
        "environment": settings.APP_ENV,
        "components": {},
    }

    # MongoDB
    try:
        db = get_database()
        await db.command("ping")
        health["components"]["mongodb"] = {"status": "healthy"}
    except Exception as exc:
        health["components"]["mongodb"] = {"status": "unhealthy", "error": str(exc)}

    # Sync status summary
    try:
        sync_counts = await _sync_log_repo.count_by_status()
        health["components"]["data_sync"] = {
            "status": "healthy" if sync_counts.get("FAILED", 0) == 0 else "degraded",
            "counts": sync_counts,
        }
    except Exception as exc:
        health["components"]["data_sync"] = {"status": "unknown", "error": str(exc)}

    # Scheduler
    try:
        scheduler = get_scheduler()
        jobs = scheduler.get_jobs() if scheduler else []
        health["components"]["scheduler"] = {
            "status": "running" if scheduler and scheduler.running else "stopped",
            "job_count": len(jobs),
        }
    except Exception as exc:
        health["components"]["scheduler"] = {"status": "unknown", "error": str(exc)}

    # Live engine
    try:
        from app.services.live_signal_service import live_signal_service
        engine_status = {
            "is_active": live_signal_service.is_active,
        }
        health["components"]["live_engine"] = {
            "status": "running" if live_signal_service.is_active else "stopped",
            **engine_status,
        }
    except Exception as exc:
        health["components"]["live_engine"] = {"status": "unknown", "error": str(exc)}

    # Overall status
    statuses = [c.get("status", "unknown") for c in health["components"].values()]
    if "unhealthy" in statuses:
        health["overall"] = "unhealthy"
    elif "degraded" in statuses or "stopped" in statuses:
        health["overall"] = "degraded"
    else:
        health["overall"] = "healthy"

    return health


# ── Scheduler jobs ────────────────────────────────────────────────────────────

@router.get(
    "/scheduler",
    summary="APScheduler job states",
    dependencies=[Depends(get_current_user)],
)
async def scheduler_jobs() -> dict[str, Any]:
    scheduler = get_scheduler()
    if not scheduler:
        return {"running": False, "jobs": []}

    jobs = []
    for job in scheduler.get_jobs():
        next_run = job.next_run_time
        jobs.append({
            "id": job.id,
            "name": job.name,
            "trigger": str(job.trigger),
            "next_run": next_run.isoformat() if next_run else None,
            "pending": job.pending,
        })

    return {
        "running": scheduler.running,
        "timezone": settings.SCHEDULER_TIMEZONE,
        "jobs": sorted(jobs, key=lambda j: j["id"]),
    }


# ── Alerts ────────────────────────────────────────────────────────────────────

@router.get(
    "/alerts",
    summary="Recent alert events",
    dependencies=[Depends(get_current_user)],
)
async def list_alerts(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    skip = (page - 1) * page_size
    events = await _alert_repo.get_recent(limit=page_size, skip=skip)
    severity_counts = await _alert_repo.count_by_severity()
    total = sum(severity_counts.values())

    return {
        "items": [
            {
                "id": str(e.id),
                "event_type": e.event_type,
                "severity": e.severity,
                "title": e.title,
                "body": e.body,
                "channel": e.channel,
                "delivered": e.delivered,
                "timestamp": e.timestamp.isoformat(),
            }
            for e in events
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        "severity_counts": severity_counts,
    }


# ── Audit logs ────────────────────────────────────────────────────────────────

@router.get(
    "/audit-logs",
    summary="Audit trail (admin only)",
    dependencies=[Depends(require_admin)],
)
async def audit_logs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    user_id: Optional[str] = Query(default=None),
    action: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    skip = (page - 1) * page_size
    logs = await _audit_repo.get_recent(
        limit=page_size, skip=skip, user_id=user_id, action=action
    )
    total = await _audit_repo.count_query(user_id=user_id, action=action)

    return {
        "items": [
            {
                "id": str(log.id),
                "username": log.username,
                "action": log.action,
                "resource": log.resource,
                "resource_id": log.resource_id,
                "detail": log.detail,
                "ip_address": log.ip_address,
                "success": log.success,
                "error_message": log.error_message,
                "timestamp": log.timestamp.isoformat(),
            }
            for log in logs
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
    }


# ── Production readiness checklist ───────────────────────────────────────────

@router.get(
    "/readiness",
    summary="Production readiness checklist",
    dependencies=[Depends(require_admin)],
)
async def readiness_checklist() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    # 1. Environment
    check(
        "production_env",
        settings.APP_ENV == "production",
        f"APP_ENV={settings.APP_ENV}",
    )

    # 2. JWT secret changed from default
    check(
        "jwt_secret_changed",
        settings.JWT_SECRET != "change-me-in-production",
        "JWT_SECRET must be changed from default",
    )

    # 3. Auth required
    check(
        "auth_required",
        settings.AUTH_REQUIRED,
        f"AUTH_REQUIRED={settings.AUTH_REQUIRED}",
    )

    # 4. Admin password changed
    check(
        "admin_password_changed",
        settings.INITIAL_ADMIN_PASSWORD != "change-me-on-first-login",
        "Change INITIAL_ADMIN_PASSWORD or create a new admin and delete default",
    )

    # 5. MongoDB connected
    db_ok = False
    try:
        db = get_database()
        await db.command("ping")
        db_ok = True
    except Exception:
        pass
    check("mongodb_connected", db_ok)

    # 6. Scheduler running
    scheduler = get_scheduler()
    check(
        "scheduler_running",
        scheduler is not None and scheduler.running,
        "APScheduler must be running before trading begins",
    )

    # 7. Alerting configured
    alerting_ok = settings.ALERT_TELEGRAM_ENABLED or settings.ALERT_EMAIL_ENABLED
    check(
        "alerting_configured",
        alerting_ok,
        "Configure Telegram or Email alerting",
    )

    # 8. Broker credentials present
    broker_ok = bool(
        settings.ANGELONE_API_KEY
        and settings.ANGELONE_CLIENT_ID
        and settings.ANGELONE_PASSWORD
        and settings.ANGELONE_TOTP_SECRET
    )
    check("broker_credentials_set", broker_ok, "AngelOne SmartAPI credentials required")

    # 9. Live execution master switch
    check(
        "live_exec_reviewed",
        settings.LIVE_EXEC_ENABLED is not None,
        f"LIVE_EXEC_ENABLED={settings.LIVE_EXEC_ENABLED}",
    )

    # 10. CORS hardened
    cors_ok = all(
        not origin.startswith("http://localhost")
        for origin in settings.CORS_ORIGINS
    )
    check(
        "cors_hardened",
        cors_ok,
        f"CORS_ORIGINS must not include localhost in production: {settings.CORS_ORIGINS}",
    )

    # 11. Sync data health
    try:
        counts = await _sync_log_repo.count_by_status()
        failed = counts.get("FAILED", 0)
        success = counts.get("SUCCESS", 0)
        check(
            "market_data_healthy",
            failed == 0 and success > 0,
            f"SUCCESS={success} FAILED={failed}",
        )
    except Exception:
        check("market_data_healthy", False, "Could not query sync logs")

    passed = sum(1 for c in checks if c["passed"])
    total = len(checks)
    return {
        "passed": passed,
        "total": total,
        "ready": passed == total,
        "checks": checks,
    }
