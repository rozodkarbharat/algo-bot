"""
Repositories for StrategyExperiment and ABTest documents.

Follows the project's raw-dict query pattern (Beanie 2.x / Pydantic v2):
no ORM-style field expressions are used — all filters are plain MongoDB dicts.
"""

from datetime import datetime, timezone

from app.models.strategy_experiment import (
    ABTest,
    ABTestStatus,
    ExperimentStatus,
    StrategyExperiment,
)
from app.repositories.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# StrategyExperimentRepository
# ---------------------------------------------------------------------------


class StrategyExperimentRepository(BaseRepository[StrategyExperiment]):
    """Data-access layer for the strategy_experiments collection."""

    document_model = StrategyExperiment

    # ── Reads ─────────────────────────────────────────────────────────────────

    async def get_by_experiment_id(
        self, experiment_id: str
    ) -> StrategyExperiment | None:
        """Return a StrategyExperiment by its UUID experiment_id, or None."""
        return await StrategyExperiment.find_one({"experiment_id": experiment_id})

    async def get_by_strategy_id(
        self,
        strategy_id: str,
        skip: int = 0,
        limit: int = 50,
    ) -> list[StrategyExperiment]:
        """Return all experiments for a given strategy_id, newest first."""
        return (
            await StrategyExperiment.find({"strategy_id": strategy_id})
            .sort("-created_at")
            .skip(skip)
            .limit(limit)
            .to_list()
        )

    async def get_by_catalog_id(self, catalog_id: str) -> list[StrategyExperiment]:
        """Return all experiments linked to a given catalog_id."""
        return await StrategyExperiment.find({"catalog_id": catalog_id}).to_list()

    async def get_by_status(
        self, status: ExperimentStatus
    ) -> list[StrategyExperiment]:
        """Return all experiments with the given status."""
        return await StrategyExperiment.find({"status": status.value}).to_list()

    # ── Writes ────────────────────────────────────────────────────────────────

    async def update_status(
        self,
        experiment_id: str,
        status: ExperimentStatus,
        backtest_run_id: str | None = None,
        results: dict | None = None,
        error: str | None = None,
    ) -> StrategyExperiment | None:
        """Transition experiment status and update linked fields.

        - Sets ``started_at`` when transitioning to RUNNING.
        - Sets ``completed_at`` when transitioning to COMPLETED or FAILED.
        - Populates ``backtest_run_id``, ``results``, and ``error_message``
          when the corresponding arguments are provided.

        Returns the updated document, or None when experiment_id is not found.
        """
        experiment = await self.get_by_experiment_id(experiment_id)
        if experiment is None:
            logger.warning(
                "update_status: experiment not found experiment_id=%s", experiment_id
            )
            return None

        experiment.status = status
        now = _utcnow()

        if status == ExperimentStatus.RUNNING:
            experiment.started_at = now
        elif status in (ExperimentStatus.COMPLETED, ExperimentStatus.FAILED):
            experiment.completed_at = now

        if backtest_run_id is not None:
            experiment.backtest_run_id = backtest_run_id

        if results is not None:
            experiment.results = results

        if error is not None:
            experiment.error_message = error

        return await self.save(experiment)


# ---------------------------------------------------------------------------
# ABTestRepository
# ---------------------------------------------------------------------------


class ABTestRepository(BaseRepository[ABTest]):
    """Data-access layer for the ab_tests collection."""

    document_model = ABTest

    # ── Reads ─────────────────────────────────────────────────────────────────

    async def get_by_ab_test_id(self, ab_test_id: str) -> ABTest | None:
        """Return an ABTest by its UUID ab_test_id, or None."""
        return await ABTest.find_one({"ab_test_id": ab_test_id})

    async def list_all(self, skip: int = 0, limit: int = 50) -> list[ABTest]:
        """Return all A/B tests, newest first, with pagination."""
        return (
            await ABTest.find({})
            .sort("-created_at")
            .skip(skip)
            .limit(limit)
            .to_list()
        )

    async def get_by_status(self, status: ABTestStatus) -> list[ABTest]:
        """Return all A/B tests with the given status."""
        return await ABTest.find({"status": status.value}).to_list()

    # ── Writes ────────────────────────────────────────────────────────────────

    async def update_results(
        self,
        ab_test_id: str,
        results_a: dict,
        results_b: dict,
        winner: str | None,
        winner_reason: str,
    ) -> ABTest | None:
        """Record both legs' results, mark the winner, and set status=COMPLETED.

        Returns the updated document, or None when ab_test_id is not found.
        """
        ab_test = await self.get_by_ab_test_id(ab_test_id)
        if ab_test is None:
            logger.warning(
                "update_results: A/B test not found ab_test_id=%s", ab_test_id
            )
            return None

        ab_test.results_a = results_a
        ab_test.results_b = results_b
        ab_test.winner = winner
        ab_test.winner_reason = winner_reason
        ab_test.status = ABTestStatus.COMPLETED
        ab_test.completed_at = _utcnow()

        return await self.save(ab_test)
