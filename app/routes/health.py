"""
Health check endpoints.

GET /health        — lightweight liveness probe (no DB call)
GET /health/ready  — readiness probe (verifies MongoDB connectivity)

Used by:
  - Docker health checks
  - Kubernetes liveness/readiness probes
  - Load balancer health checks
"""

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database.mongodb import get_database
from app.config.settings import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["Health"])


@router.get("/health", summary="Liveness probe")
async def health_check() -> dict[str, Any]:
    """
    Quick liveness check — always returns 200 if the process is alive.
    Does NOT verify database connectivity (use /health/ready for that).
    """
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "env": settings.APP_ENV,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }


@router.get("/health/ready", summary="Readiness probe")
async def readiness_check() -> dict[str, Any]:
    """
    Readiness check — verifies that MongoDB is reachable.
    Returns 200 on success, 503 on failure.
    """
    db_status = "ok"
    db_error: str | None = None

    try:
        db: AsyncIOMotorDatabase = get_database()  # type: ignore[type-arg]
        await db.command("ping")
    except Exception as exc:
        db_status = "unreachable"
        db_error = str(exc)
        logger.error("Readiness check failed — MongoDB unreachable: %s", exc)

    from fastapi import HTTPException
    if db_status != "ok":
        raise HTTPException(
            status_code=503,
            detail={
                "status": "unavailable",
                "database": db_status,
                "error": db_error,
            },
        )

    return {
        "status": "ready",
        "database": db_status,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }
