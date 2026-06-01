"""
Notification management API.

Endpoints:
  GET  /api/v1/notifications/alerts          — paginated alert history
  GET  /api/v1/notifications/alerts/{id}     — single alert detail
  GET  /api/v1/notifications/stats           — count by severity / channel
  POST /api/v1/notifications/test            — send a test notification (admin)
  GET  /api/v1/notifications/settings        — current notification settings
  PATCH /api/v1/notifications/settings       — toggle categories (admin)
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.models.alert_event import AlertEvent, AlertSeverity, AlertChannel
from app.repositories.alert_event_repository import AlertEventRepository
from app.schemas.common import PaginatedResponse
from app.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()
_repo = AlertEventRepository()


# ── Response schemas ──────────────────────────────────────────────────────────

class AlertEventResponse(BaseModel):
    id: str
    event_type: str
    severity: str
    title: str
    body: str
    channel: str
    delivered: bool
    dedup_key: Optional[str]
    timestamp: str
    payload: Optional[dict] = None

    @classmethod
    def from_doc(cls, doc: AlertEvent) -> "AlertEventResponse":
        return cls(
            id=str(doc.id),
            event_type=doc.event_type,
            severity=doc.severity,
            title=doc.title,
            body=doc.body,
            channel=doc.channel,
            delivered=doc.delivered,
            dedup_key=doc.dedup_key,
            timestamp=doc.timestamp.isoformat(),
            payload=doc.payload,
        )


class NotificationStatsResponse(BaseModel):
    total: int
    delivered: int
    failed: int
    by_severity: dict[str, int]
    by_channel: dict[str, int]


class NotificationSettingsResponse(BaseModel):
    enabled: bool
    trade_alerts: bool
    signal_alerts: bool
    system_alerts: bool
    daily_summary: bool
    throttle_window_seconds: int
    telegram_enabled: bool
    email_enabled: bool


class NotificationSettingsPatch(BaseModel):
    trade_alerts: Optional[bool] = None
    signal_alerts: Optional[bool] = None
    system_alerts: Optional[bool] = None
    daily_summary: Optional[bool] = None


class TestNotificationRequest(BaseModel):
    channel: str = "system"
    message: str = "TradingBot test notification"


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=PaginatedResponse, summary="List notifications (alias for /alerts)")
async def list_notifications_root(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    severity: Optional[str] = Query(default=None),
    event_type: Optional[str] = Query(default=None),
) -> PaginatedResponse:
    """Root GET — returns the same paginated alert history as /alerts."""
    filters: dict = {}
    if severity:
        filters["severity"] = severity
    if event_type:
        filters["event_type"] = event_type
    skip = (page - 1) * page_size
    try:
        total = await AlertEvent.find(filters).count()
        docs = (
            await AlertEvent.find(filters)
            .sort("-timestamp")
            .skip(skip)
            .limit(page_size)
            .to_list()
        )
        items = [AlertEventResponse.from_doc(d) for d in docs]
        pages = max(1, (total + page_size - 1) // page_size)
        return PaginatedResponse(
            items=[i.model_dump() for i in items],
            total=total,
            page=page,
            page_size=page_size,
            pages=pages,
        )
    except Exception as exc:
        logger.error("list_notifications_root failed: %s", exc)
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail="Failed to fetch notifications")


@router.get("/alerts", response_model=PaginatedResponse)
async def list_alerts(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    severity: Optional[str] = Query(default=None),
    event_type: Optional[str] = Query(default=None),
    delivered: Optional[bool] = Query(default=None),
) -> PaginatedResponse:
    """Return paginated alert history with optional filters."""
    filters: dict = {}
    if severity:
        filters["severity"] = severity
    if event_type:
        filters["event_type"] = event_type
    if delivered is not None:
        filters["delivered"] = delivered

    skip = (page - 1) * page_size
    try:
        total = await AlertEvent.find(filters).count()
        docs = (
            await AlertEvent.find(filters)
            .sort("-timestamp")
            .skip(skip)
            .limit(page_size)
            .to_list()
        )
        items = [AlertEventResponse.from_doc(d) for d in docs]
        pages = max(1, (total + page_size - 1) // page_size)
        return PaginatedResponse(
            items=[i.model_dump() for i in items],
            total=total,
            page=page,
            page_size=page_size,
            pages=pages,
        )
    except Exception as exc:
        logger.error("list_alerts failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to fetch alerts")


@router.get("/alerts/{alert_id}", response_model=AlertEventResponse)
async def get_alert(alert_id: str) -> AlertEventResponse:
    """Return a single AlertEvent by its MongoDB _id."""
    from beanie import PydanticObjectId
    try:
        doc = await AlertEvent.get(PydanticObjectId(alert_id))
    except Exception:
        doc = None
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found")
    return AlertEventResponse.from_doc(doc)


@router.get("/stats", response_model=NotificationStatsResponse)
async def get_notification_stats() -> NotificationStatsResponse:
    """Return aggregate notification statistics."""
    try:
        total = await AlertEvent.find({}).count()
        delivered_count = await AlertEvent.find({"delivered": True}).count()

        severity_pipeline = [
            {"$group": {"_id": "$severity", "count": {"$sum": 1}}}
        ]
        channel_pipeline = [
            {"$group": {"_id": "$channel", "count": {"$sum": 1}}}
        ]
        sev_results = await AlertEvent.aggregate(severity_pipeline).to_list()
        ch_results = await AlertEvent.aggregate(channel_pipeline).to_list()

        by_severity = {r["_id"]: r["count"] for r in sev_results}
        by_channel = {r["_id"]: r["count"] for r in ch_results}

        return NotificationStatsResponse(
            total=total,
            delivered=delivered_count,
            failed=total - delivered_count,
            by_severity=by_severity,
            by_channel=by_channel,
        )
    except Exception as exc:
        logger.error("get_notification_stats failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to fetch stats")


@router.get("/settings", response_model=NotificationSettingsResponse)
async def get_notification_settings() -> NotificationSettingsResponse:
    """Return current notification configuration (read-only view)."""
    from app.config.settings import settings as s
    return NotificationSettingsResponse(
        enabled=s.NOTIFY_ENABLED,
        trade_alerts=s.NOTIFY_TRADE_ALERTS,
        signal_alerts=s.NOTIFY_SIGNAL_ALERTS,
        system_alerts=s.NOTIFY_SYSTEM_ALERTS,
        daily_summary=s.NOTIFY_DAILY_SUMMARY,
        throttle_window_seconds=s.NOTIFY_THROTTLE_WINDOW_SECONDS,
        telegram_enabled=s.ALERT_TELEGRAM_ENABLED,
        email_enabled=s.ALERT_EMAIL_ENABLED,
    )


@router.post("/test")
async def send_test_notification(body: TestNotificationRequest) -> dict:
    """
    Send a test notification through the specified channel.

    Useful for verifying credentials after initial setup.
    Admin-only in production.
    """
    from app.services.notification_service import notification_service
    try:
        await notification_service.on_system_error(
            component="test",
            error=body.message,
            detail="This is a test notification from /api/v1/notifications/test",
        )
        return {"status": "sent", "message": body.message, "channel": body.channel}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Test notification failed: {exc}")
