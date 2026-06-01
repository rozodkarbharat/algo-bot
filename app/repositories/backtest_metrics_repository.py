"""
BacktestMetrics repository — data-access layer for the backtest_metrics collection.

One metrics document per run. Uses upsert so metrics can be recomputed
without creating duplicates.
"""

from typing import Optional

from app.core.exceptions import DatabaseException
from app.models.backtest_metrics import BacktestMetrics
from app.repositories.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class BacktestMetricsRepository(BaseRepository[BacktestMetrics]):
    document_model = BacktestMetrics

    # ── Writes ────────────────────────────────────────────────────────────────

    async def upsert_metrics(self, metrics: BacktestMetrics) -> BacktestMetrics:
        """
        Insert or replace the metrics document for a run.

        Uses Motor upsert to avoid duplicates when re-computing metrics
        for the same run_id.
        """
        try:
            collection = BacktestMetrics.get_pymongo_collection()
            doc = metrics.model_dump(exclude={"id"})
            result = await collection.update_one(
                {"run_id": metrics.run_id},
                {"$set": doc},
                upsert=True,
            )
            if result.upserted_id:
                metrics.id = result.upserted_id  # type: ignore[assignment]
            return metrics
        except Exception as exc:
            logger.error("upsert_metrics failed for run_id=%s: %s", metrics.run_id, exc)
            raise DatabaseException(
                f"Failed to upsert BacktestMetrics for run {metrics.run_id}.",
                detail=str(exc),
            )

    # ── Reads ─────────────────────────────────────────────────────────────────

    async def get_by_run_id(self, run_id: str) -> Optional[BacktestMetrics]:
        """Return the metrics document for a run, or None."""
        try:
            return await BacktestMetrics.find_one({"run_id": run_id})
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch BacktestMetrics for run {run_id}.", detail=str(exc)
            )

    async def delete_by_run_id(self, run_id: str) -> bool:
        """Delete metrics for a run. Returns True if deleted."""
        try:
            collection = BacktestMetrics.get_pymongo_collection()
            result = await collection.delete_one({"run_id": run_id})
            return result.deleted_count > 0
        except Exception as exc:
            raise DatabaseException(
                f"Failed to delete BacktestMetrics for run {run_id}.", detail=str(exc)
            )
