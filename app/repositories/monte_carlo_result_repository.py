"""
Repository for MonteCarloResult documents.

Raw-dict query pattern — no ORM-style Beanie field expressions (Beanie 2.x / Pydantic v2).
"""

from typing import Optional

from pymongo import InsertOne

from app.core.exceptions import DatabaseException
from app.models.monte_carlo_result import MonteCarloResult
from app.repositories.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class MonteCarloResultRepository(BaseRepository[MonteCarloResult]):
    document_model = MonteCarloResult

    # ── Reads ─────────────────────────────────────────────────────────────────

    async def get_results_for_run(self, run_id: str) -> list[MonteCarloResult]:
        """Return all results for a run (one per strategy + one for portfolio)."""
        return await MonteCarloResult.find({"run_id": run_id}).to_list()

    async def get_result_for_strategy(
        self, run_id: str, strategy_id: str
    ) -> Optional[MonteCarloResult]:
        return await MonteCarloResult.find_one(
            {"run_id": run_id, "strategy_id": strategy_id}
        )

    # ── Writes ────────────────────────────────────────────────────────────────

    async def create_result(self, result: MonteCarloResult) -> MonteCarloResult:
        return await self.create(result)

    async def bulk_insert(self, results: list[MonteCarloResult]) -> int:
        """Insert all result documents in one bulk_write call."""
        if not results:
            return 0
        try:
            collection = MonteCarloResult.get_pymongo_collection()
            operations = [InsertOne(r.model_dump(exclude={"id"})) for r in results]
            result = await collection.bulk_write(operations, ordered=False)
            logger.debug(
                "bulk_insert: %d MonteCarloResult docs inserted for run_id=%s",
                result.inserted_count,
                results[0].run_id,
            )
            return result.inserted_count
        except Exception as exc:
            logger.error("MonteCarloResult bulk_insert failed: %s", exc, exc_info=True)
            raise DatabaseException(
                "Bulk insert of MonteCarloResult records failed.", detail=str(exc)
            )
