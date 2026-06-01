"""
Repository for ResearchRun documents.

Follows the same raw-dict query pattern as every other repository in this
codebase — no ORM-style Beanie field expressions (Beanie 2.x / Pydantic v2).
"""

from typing import Optional

from app.models.research_run import ResearchRun, ResearchRunStatus
from app.repositories.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ResearchRunRepository(BaseRepository[ResearchRun]):
    document_model = ResearchRun

    # ── Reads ─────────────────────────────────────────────────────────────────

    async def get_by_run_id(self, run_id: str) -> Optional[ResearchRun]:
        """Return a ResearchRun by its run_id, or None if not found."""
        return await ResearchRun.find_one({"run_id": run_id})

    async def list_runs(
        self,
        status: Optional[ResearchRunStatus] = None,
        limit: int = 20,
        skip: int = 0,
    ) -> list[ResearchRun]:
        """Return research runs, newest first, with optional status filter."""
        query: dict = {}
        if status is not None:
            query["status"] = status.value
        return (
            await ResearchRun.find(query)
            .sort("-created_at")
            .skip(skip)
            .limit(limit)
            .to_list()
        )

    async def count_runs(self, status: Optional[ResearchRunStatus] = None) -> int:
        """Return total count with optional status filter."""
        query: dict = {}
        if status is not None:
            query["status"] = status.value
        return await ResearchRun.find(query).count()

    # ── Writes ────────────────────────────────────────────────────────────────

    async def create_run(self, run: ResearchRun) -> ResearchRun:
        """Insert a new ResearchRun and return the persisted instance."""
        return await self.create(run)

    async def update_run(self, run: ResearchRun) -> ResearchRun:
        """Persist changes to an existing ResearchRun."""
        return await self.save(run)
