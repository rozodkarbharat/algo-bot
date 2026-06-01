"""
NotificationEvent repository — CRUD for the notification_events collection.
"""

from datetime import datetime
from typing import Optional

from app.models.notification_event import NotificationEvent, NotificationSeverity
from app.repositories.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class NotificationEventRepository(BaseRepository[NotificationEvent]):

    document_model = NotificationEvent

    async def get_recent(
        self,
        limit: int = 100,
        skip: int = 0,
        event_type: Optional[str] = None,
        source: Optional[str] = None,
        severity: Optional[NotificationSeverity] = None,
    ) -> list[NotificationEvent]:
        query: dict = {}
        if event_type:
            query["event_type"] = event_type
        if source:
            query["source"] = source
        if severity:
            query["severity"] = severity
        return (
            await NotificationEvent.find(query)
            .sort("-created_at")
            .skip(skip)
            .limit(limit)
            .to_list()
        )

    async def count_query(
        self,
        event_type: Optional[str] = None,
        source: Optional[str] = None,
        severity: Optional[NotificationSeverity] = None,
    ) -> int:
        query: dict = {}
        if event_type:
            query["event_type"] = event_type
        if source:
            query["source"] = source
        if severity:
            query["severity"] = severity
        return await NotificationEvent.find(query).count()

    async def get_by_event_id(self, event_id: str) -> Optional[NotificationEvent]:
        return await NotificationEvent.find_one({"event_id": event_id})

    async def get_since(
        self, since: datetime, limit: int = 500
    ) -> list[NotificationEvent]:
        return (
            await NotificationEvent.find({"created_at": {"$gte": since}})
            .sort("-created_at")
            .limit(limit)
            .to_list()
        )
