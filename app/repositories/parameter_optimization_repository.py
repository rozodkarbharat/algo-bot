"""
Repository for ParameterOptimizationResult documents.

Supports bulk-insertion of all results from a sweep, and ranked retrieval
for building optimization leaderboards.
"""

from typing import Optional

from pymongo import InsertOne

from app.models.parameter_optimization_result import ParameterOptimizationResult
from app.repositories.base_repository import BaseRepository
from app.core.exceptions import DatabaseException
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ParameterOptimizationRepository(BaseRepository[ParameterOptimizationResult]):
    document_model = ParameterOptimizationResult

    # ── Reads ─────────────────────────────────────────────────────────────────

    async def get_by_run_id(
        self,
        run_id: str,
        parameter_name: Optional[str] = None,
    ) -> list[ParameterOptimizationResult]:
        """
        Return all optimization results for a run, optionally filtered to one parameter.

        Results are returned sorted by total_pnl descending (best first).
        """
        query: dict = {"run_id": run_id}
        if parameter_name is not None:
            query["parameter_name"] = parameter_name
        return (
            await ParameterOptimizationResult.find(query)
            .sort("-total_pnl")
            .to_list()
        )

    async def get_best_by_parameter(
        self,
        run_id: str,
        parameter_name: str,
        metric: str = "total_pnl",
        limit: int = 5,
    ) -> list[ParameterOptimizationResult]:
        """Return top-N results for a single parameter, sorted by the given metric."""
        sort_key = f"-{metric}"
        return (
            await ParameterOptimizationResult.find(
                {"run_id": run_id, "parameter_name": parameter_name}
            )
            .sort(sort_key)
            .limit(limit)
            .to_list()
        )

    async def count_by_run_id(self, run_id: str) -> int:
        return await ParameterOptimizationResult.find({"run_id": run_id}).count()

    # ── Writes ────────────────────────────────────────────────────────────────

    async def bulk_insert(
        self, results: list[ParameterOptimizationResult]
    ) -> int:
        """
        Insert many ParameterOptimizationResult documents in a single Motor call.

        Returns the number of documents written.
        """
        if not results:
            return 0
        try:
            collection = ParameterOptimizationResult.get_pymongo_collection()
            ops = [InsertOne(r.model_dump(exclude={"id"})) for r in results]
            bulk_result = await collection.bulk_write(ops, ordered=False)
            count = bulk_result.inserted_count
            logger.debug(
                "ParameterOptimizationRepository.bulk_insert: %d docs written.", count
            )
            return count
        except Exception as exc:
            logger.error("bulk_insert failed: %s", exc)
            raise DatabaseException("Failed to bulk-insert optimization results.", detail=str(exc))
