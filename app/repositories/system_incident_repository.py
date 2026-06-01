"""Repository for SystemIncident documents."""

from __future__ import annotations

from typing import Optional

from app.core.exceptions import DatabaseException
from app.models.system_incident import IncidentStatus, SystemIncident
from app.repositories.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class SystemIncidentRepository(BaseRepository[SystemIncident]):
    document_model = SystemIncident

    async def get_by_id(self, incident_id: str) -> Optional[SystemIncident]:
        try:
            return await SystemIncident.find_one({"incident_id": incident_id})
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch incident {incident_id}", detail=str(exc)
            ) from exc

    async def list_open(self, component: Optional[str] = None) -> list[SystemIncident]:
        try:
            query: dict = {
                "status": {"$in": [IncidentStatus.OPEN, IncidentStatus.INVESTIGATING]}
            }
            if component:
                query["component"] = component
            return (
                await SystemIncident.find(query).sort("-detected_at").to_list()
            )
        except Exception as exc:
            raise DatabaseException("Failed to list open incidents", detail=str(exc)) from exc

    async def list_recent(self, limit: int = 50, component: Optional[str] = None) -> list[SystemIncident]:
        try:
            query: dict = {}
            if component:
                query["component"] = component
            return (
                await SystemIncident.find(query)
                .sort("-detected_at")
                .limit(limit)
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException("Failed to list incidents", detail=str(exc)) from exc
