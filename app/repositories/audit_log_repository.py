"""
AuditLog repository.
"""

from datetime import datetime
from typing import Optional

from app.models.audit_log import AuditLog
from app.repositories.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class AuditLogRepository(BaseRepository[AuditLog]):

    document_model = AuditLog

    async def get_recent(
        self,
        limit: int = 50,
        skip: int = 0,
        user_id: Optional[str] = None,
        action: Optional[str] = None,
    ) -> list[AuditLog]:
        query: dict = {}
        if user_id:
            query["user_id"] = user_id
        if action:
            query["action"] = action
        return (
            await AuditLog.find(query)
            .sort("-timestamp")
            .skip(skip)
            .limit(limit)
            .to_list()
        )

    async def count_query(
        self,
        user_id: Optional[str] = None,
        action: Optional[str] = None,
    ) -> int:
        query: dict = {}
        if user_id:
            query["user_id"] = user_id
        if action:
            query["action"] = action
        return await AuditLog.find(query).count()
