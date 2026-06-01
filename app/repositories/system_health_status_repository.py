"""Repository for SystemHealthStatus documents."""

from __future__ import annotations

from typing import Optional

from app.core.exceptions import DatabaseException
from app.models.system_health_status import ComponentStatus, SystemHealthStatus
from app.repositories.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class SystemHealthStatusRepository(BaseRepository[SystemHealthStatus]):
    document_model = SystemHealthStatus

    async def upsert(self, status: SystemHealthStatus) -> None:
        """Insert or replace by component_name."""
        try:
            collection = SystemHealthStatus.get_motor_collection()
            doc = status.model_dump(mode="python")
            doc.pop("id", None)
            await collection.update_one(
                {"component_name": status.component_name},
                {"$set": doc},
                upsert=True,
            )
        except Exception as exc:
            raise DatabaseException(
                f"Failed to upsert SystemHealthStatus for {status.component_name}",
                detail=str(exc),
            ) from exc

    async def get_by_component(
        self, component_name: str
    ) -> Optional[SystemHealthStatus]:
        try:
            return await SystemHealthStatus.find_one({"component_name": component_name})
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch health status for {component_name}",
                detail=str(exc),
            ) from exc

    async def get_all(self) -> list[SystemHealthStatus]:
        try:
            return await SystemHealthStatus.find({}).sort("component_name").to_list()
        except Exception as exc:
            raise DatabaseException(
                "Failed to fetch all system health statuses",
                detail=str(exc),
            ) from exc

    async def get_unhealthy(self) -> list[SystemHealthStatus]:
        """Return components that are not in HEALTHY state."""
        try:
            return await SystemHealthStatus.find(
                {"status": {"$in": [ComponentStatus.DEGRADED, ComponentStatus.UNHEALTHY]}}
            ).to_list()
        except Exception as exc:
            raise DatabaseException(
                "Failed to fetch unhealthy statuses",
                detail=str(exc),
            ) from exc
