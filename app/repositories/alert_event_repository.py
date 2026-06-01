"""
AlertEvent repository.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional

from app.models.alert_event import AlertEvent
from app.repositories.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class AlertEventRepository(BaseRepository[AlertEvent]):
    document_model = AlertEvent

    async def get_recent(self, limit: int = 100, skip: int = 0) -> list[AlertEvent]:
        return (
            await AlertEvent.find({})
            .sort("-timestamp")
            .skip(skip)
            .limit(limit)
            .to_list()
        )

    async def find_recent_by_dedup_key(
        self, dedup_key: str, within_seconds: int = 300
    ) -> Optional[AlertEvent]:
        """Return the most recent alert with this dedup_key within the cooldown window."""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=within_seconds)
        return await AlertEvent.find_one(
            {"dedup_key": dedup_key, "timestamp": {"$gte": cutoff}},
            sort=[("-timestamp", 1)],
        )

    async def get_undelivered(self) -> list[AlertEvent]:
        return await AlertEvent.find({"delivered": False}).to_list()

    async def count_by_severity(self) -> dict[str, int]:
        pipeline = [
            {"$group": {"_id": "$severity", "count": {"$sum": 1}}},
        ]
        results = await AlertEvent.aggregate(pipeline).to_list()
        return {r["_id"]: r["count"] for r in results}
