"""
Repository for MonteCarloRun documents.

Raw-dict query pattern — no ORM-style Beanie field expressions (Beanie 2.x / Pydantic v2).
"""

from typing import Optional

from app.models.monte_carlo_run import MonteCarloRun, MonteCarloRunStatus
from app.repositories.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class MonteCarloRunRepository(BaseRepository[MonteCarloRun]):
    document_model = MonteCarloRun

    # ── Reads ─────────────────────────────────────────────────────────────────

    async def get_by_run_id(self, run_id: str) -> Optional[MonteCarloRun]:
        return await MonteCarloRun.find_one({"run_id": run_id})

    async def list_runs(
        self,
        status: Optional[MonteCarloRunStatus] = None,
        limit: int = 20,
        skip: int = 0,
    ) -> list[MonteCarloRun]:
        query: dict = {}
        if status is not None:
            query["status"] = status.value
        return (
            await MonteCarloRun.find(query)
            .sort("-created_at")
            .skip(skip)
            .limit(limit)
            .to_list()
        )

    async def count_runs(
        self, status: Optional[MonteCarloRunStatus] = None
    ) -> int:
        query: dict = {}
        if status is not None:
            query["status"] = status.value
        return await MonteCarloRun.find(query).count()

    # ── Writes ────────────────────────────────────────────────────────────────

    async def create_run(self, run: MonteCarloRun) -> MonteCarloRun:
        return await self.create(run)

    async def update_run(self, run: MonteCarloRun) -> MonteCarloRun:
        return await self.save(run)
