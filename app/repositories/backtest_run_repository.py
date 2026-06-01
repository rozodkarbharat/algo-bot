"""
BacktestRun repository — data-access layer for the backtest_runs collection.

Uses raw MongoDB filter dicts (Beanie 2.x / Pydantic v2 requirement).
"""

from typing import Optional

from pymongo import DESCENDING

from app.core.exceptions import DatabaseException
from app.models.backtest_run import BacktestRun, BacktestRunStatus
from app.repositories.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class BacktestRunRepository(BaseRepository[BacktestRun]):
    document_model = BacktestRun

    # ── Writes ────────────────────────────────────────────────────────────────

    async def create_run(self, run: BacktestRun) -> BacktestRun:
        """Insert a new backtest run document."""
        try:
            return await run.insert()
        except Exception as exc:
            logger.error("create_run failed: %s", exc)
            raise DatabaseException("Failed to create BacktestRun.", detail=str(exc))

    async def update_run(self, run: BacktestRun) -> BacktestRun:
        """Persist updates to an existing BacktestRun document."""
        try:
            await run.save()
            return run
        except Exception as exc:
            logger.error("update_run failed for run_id=%s: %s", run.run_id, exc)
            raise DatabaseException(
                f"Failed to update BacktestRun {run.run_id}.", detail=str(exc)
            )

    # ── Reads ─────────────────────────────────────────────────────────────────

    async def get_by_run_id(self, run_id: str) -> Optional[BacktestRun]:
        """Return a run by its UUID run_id, or None."""
        try:
            return await BacktestRun.find_one({"run_id": run_id})
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch BacktestRun {run_id}.", detail=str(exc)
            )

    async def list_runs(
        self,
        strategy_name: Optional[str] = None,
        status: Optional[BacktestRunStatus] = None,
        limit: int = 50,
        skip: int = 0,
    ) -> list[BacktestRun]:
        """Return paginated runs, newest first. Supports optional filters."""
        try:
            query: dict = {}
            if strategy_name:
                query["strategy_name"] = strategy_name
            if status:
                query["status"] = status.value
            return (
                await BacktestRun.find(query)
                .sort("-created_at")
                .skip(skip)
                .limit(limit)
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException("Failed to list BacktestRuns.", detail=str(exc))

    async def count_runs(
        self,
        strategy_name: Optional[str] = None,
        status: Optional[BacktestRunStatus] = None,
    ) -> int:
        """Return total count matching the filters."""
        try:
            query: dict = {}
            if strategy_name:
                query["strategy_name"] = strategy_name
            if status:
                query["status"] = status.value
            return await BacktestRun.find(query).count()
        except Exception as exc:
            raise DatabaseException("Failed to count BacktestRuns.", detail=str(exc))

    async def get_latest_run(
        self, strategy_name: Optional[str] = None
    ) -> Optional[BacktestRun]:
        """Return the most recently created run."""
        try:
            query: dict = {}
            if strategy_name:
                query["strategy_name"] = strategy_name
            results = (
                await BacktestRun.find(query)
                .sort("-created_at")
                .limit(1)
                .to_list()
            )
            return results[0] if results else None
        except Exception as exc:
            raise DatabaseException("Failed to fetch latest BacktestRun.", detail=str(exc))
