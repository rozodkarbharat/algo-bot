"""
Repository for WalkForwardRun documents.

Follows the same raw-dict query pattern as every other repository in this
codebase — no ORM-style Beanie field expressions (Beanie 2.x / Pydantic v2).
"""

from typing import Optional

from app.models.walk_forward_run import WalkForwardRun, WalkForwardRunStatus
from app.repositories.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class WalkForwardRunRepository(BaseRepository[WalkForwardRun]):
    document_model = WalkForwardRun

    # ── Reads ─────────────────────────────────────────────────────────────────

    async def get_by_run_id(self, run_id: str) -> Optional[WalkForwardRun]:
        """Return a WalkForwardRun by its run_id, or None if not found."""
        return await WalkForwardRun.find_one({"run_id": run_id})

    async def list_runs(
        self,
        status: Optional[WalkForwardRunStatus] = None,
        limit: int = 20,
        skip: int = 0,
    ) -> list[WalkForwardRun]:
        """Return walk-forward runs, newest first, with optional status filter."""
        query: dict = {}
        if status is not None:
            query["status"] = status.value
        return (
            await WalkForwardRun.find(query)
            .sort("-created_at")
            .skip(skip)
            .limit(limit)
            .to_list()
        )

    async def count_runs(self, status: Optional[WalkForwardRunStatus] = None) -> int:
        """Return total count with optional status filter."""
        query: dict = {}
        if status is not None:
            query["status"] = status.value
        return await WalkForwardRun.find(query).count()

    # ── Writes ────────────────────────────────────────────────────────────────

    async def create_run(self, run: WalkForwardRun) -> WalkForwardRun:
        """Insert a new WalkForwardRun and return the persisted instance."""
        return await self.create(run)

    async def update_run(self, run: WalkForwardRun) -> WalkForwardRun:
        """Persist changes to an existing WalkForwardRun."""
        return await self.save(run)
