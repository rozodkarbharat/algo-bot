"""
StrategyLabService — orchestrates strategy catalog management, version history,
experiment runs, A/B tests, scorecards, and the promotion/retirement pipeline.

Architecture:
  - Delegates all persistence to repositories. No direct MongoDB access.
  - BacktestService integration is stubbed; see inline comments for full wiring.
  - All public methods are async.
"""

from datetime import datetime, timezone
from dataclasses import dataclass
import uuid

from app.models.strategy_catalog import (
    StrategyCatalog,
    StrategyVersion,
    StrategyDeployment,
    StrategyStatus,
    VALID_TRANSITIONS,
)
from app.models.strategy_experiment import (
    StrategyExperiment,
    ABTest,
    ExperimentStatus,
    ABTestStatus,
)
from app.models.strategy_scorecard import StrategyScorecard, ScorecardDataSource
from app.repositories.strategy_catalog_repository import (
    StrategyCatalogRepository,
    StrategyVersionRepository,
    StrategyDeploymentRepository,
)
from app.repositories.strategy_experiment_repository import (
    StrategyExperimentRepository,
    ABTestRepository,
)
from app.repositories.strategy_scorecard_repository import StrategyScorecardRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StrategyLabService:
    """High-level orchestration for the strategy research lab."""

    def __init__(self) -> None:
        self._catalog_repo = StrategyCatalogRepository()
        self._version_repo = StrategyVersionRepository()
        self._deployment_repo = StrategyDeploymentRepository()
        self._experiment_repo = StrategyExperimentRepository()
        self._ab_test_repo = ABTestRepository()
        self._scorecard_repo = StrategyScorecardRepository()

    # =========================================================================
    # CATALOG MANAGEMENT
    # =========================================================================

    async def list_catalog(
        self, status_filter: StrategyStatus | None = None
    ) -> list[StrategyCatalog]:
        if status_filter is not None:
            return await self._catalog_repo.get_by_status(status_filter)
        return await self._catalog_repo.list_all()

    async def get_catalog_entry(self, strategy_id: str) -> StrategyCatalog:
        catalog = await self._catalog_repo.get_by_strategy_id(strategy_id)
        if catalog is None:
            raise ValueError(f"Strategy {strategy_id} not found in catalog")
        return catalog

    async def register_strategy(
        self,
        strategy_id: str,
        description: str = "",
        category: str = "",
        tags: list[str] | None = None,
    ) -> StrategyCatalog:
        from app.strategy.strategy_registry import registry

        existing = await self._catalog_repo.get_by_strategy_id(strategy_id)
        if existing is not None:
            raise ValueError(f"Strategy {strategy_id} is already registered in the catalog")

        strategy = registry.get(strategy_id)
        metadata = strategy.get_metadata()

        catalog = StrategyCatalog(
            strategy_id=strategy_id,
            strategy_name=metadata.strategy_name,
            current_version="1.0.0",
            status=StrategyStatus.DEVELOPMENT,
            description=description or metadata.description,
            category=category or metadata.category,
            tags=tags or [],
            metadata={
                "version": metadata.version,
                "parameters_schema": metadata.parameters,
            },
        )
        saved_catalog = await self._catalog_repo.create(catalog)

        initial_version = StrategyVersion(
            catalog_id=saved_catalog.catalog_id,
            strategy_id=strategy_id,
            version="1.0.0",
            parameters=strategy.get_default_config(),
            change_notes="Initial registration",
            created_by="system",
        )
        await self._version_repo.create(initial_version)

        logger.info(
            "Registered strategy strategy_id=%s catalog_id=%s",
            strategy_id,
            saved_catalog.catalog_id,
        )
        return saved_catalog

    async def list_versions(self, strategy_id: str) -> list[StrategyVersion]:
        catalog = await self.get_catalog_entry(strategy_id)
        return await self._version_repo.get_by_catalog_id(catalog.catalog_id)

    async def add_version(
        self,
        strategy_id: str,
        version: str,
        parameters: dict,
        change_notes: str = "",
        created_by: str = "system",
    ) -> StrategyVersion:
        catalog = await self.get_catalog_entry(strategy_id)

        existing = await self._version_repo.get_by_version(catalog.catalog_id, version)
        if existing is not None:
            raise ValueError(
                f"Version {version} already exists for strategy {strategy_id}"
            )

        new_version = StrategyVersion(
            catalog_id=catalog.catalog_id,
            strategy_id=strategy_id,
            version=version,
            parameters=parameters,
            change_notes=change_notes,
            created_by=created_by,
        )
        saved_version = await self._version_repo.create(new_version)

        catalog.current_version = version
        catalog.updated_at = _utcnow()
        await self._catalog_repo.save(catalog)

        logger.info(
            "Added version strategy_id=%s version=%s", strategy_id, version
        )
        return saved_version

    # =========================================================================
    # PROMOTION PIPELINE
    # =========================================================================

    async def promote_strategy(
        self,
        strategy_id: str,
        approved_by: str = "system",
        notes: str = "",
    ) -> StrategyCatalog:
        catalog = await self.get_catalog_entry(strategy_id)
        current_status = catalog.status

        if current_status == StrategyStatus.RETIRED:
            raise ValueError(f"Strategy {strategy_id} is already RETIRED and cannot be promoted")

        valid_next = VALID_TRANSITIONS.get(current_status, [])
        non_retired_next = [s for s in valid_next if s != StrategyStatus.RETIRED]
        if not non_retired_next:
            raise ValueError(
                f"No valid forward promotion path from status {current_status} for strategy {strategy_id}"
            )

        next_status = non_retired_next[0]

        if next_status == StrategyStatus.PAPER:
            experiments = await self._experiment_repo.get_by_strategy_id(strategy_id)
            completed = [e for e in experiments if e.status == ExperimentStatus.COMPLETED]
            if not completed:
                raise ValueError(
                    f"Cannot promote {strategy_id} to PAPER: at least one COMPLETED experiment is required"
                )

        if next_status == StrategyStatus.LIVE:
            scorecard = await self._scorecard_repo.get_latest_for_strategy(strategy_id)
            if scorecard is None:
                raise ValueError(
                    f"Cannot promote {strategy_id} to LIVE: no scorecard found"
                )
            if (scorecard.overall_score or 0.0) < 50.0:
                raise ValueError(
                    f"Cannot promote {strategy_id} to LIVE: overall_score "
                    f"{scorecard.overall_score} is below the required 50.0"
                )

        deployment = StrategyDeployment(
            catalog_id=catalog.catalog_id,
            strategy_id=strategy_id,
            from_status=current_status,
            to_status=next_status,
            version=catalog.current_version,
            approved_by=approved_by,
            notes=notes,
        )
        await self._deployment_repo.create(deployment)

        catalog.status = next_status
        catalog.updated_at = _utcnow()
        updated_catalog = await self._catalog_repo.save(catalog)

        logger.info(
            "Promoted strategy strategy_id=%s from=%s to=%s approved_by=%s",
            strategy_id,
            current_status,
            next_status,
            approved_by,
        )
        return updated_catalog

    async def retire_strategy(
        self,
        strategy_id: str,
        approved_by: str = "system",
        notes: str = "",
    ) -> StrategyCatalog:
        catalog = await self.get_catalog_entry(strategy_id)
        current_status = catalog.status

        if current_status == StrategyStatus.RETIRED:
            raise ValueError(f"Strategy {strategy_id} is already RETIRED")

        valid_next = VALID_TRANSITIONS.get(current_status, [])
        if StrategyStatus.RETIRED not in valid_next:
            raise ValueError(
                f"Cannot retire strategy {strategy_id} from status {current_status}"
            )

        deployment = StrategyDeployment(
            catalog_id=catalog.catalog_id,
            strategy_id=strategy_id,
            from_status=current_status,
            to_status=StrategyStatus.RETIRED,
            version=catalog.current_version,
            approved_by=approved_by,
            notes=notes,
        )
        await self._deployment_repo.create(deployment)

        catalog.status = StrategyStatus.RETIRED
        catalog.updated_at = _utcnow()
        updated_catalog = await self._catalog_repo.save(catalog)

        logger.info(
            "Retired strategy strategy_id=%s from=%s approved_by=%s",
            strategy_id,
            current_status,
            approved_by,
        )
        return updated_catalog

    async def get_lifecycle(self, strategy_id: str) -> dict:
        catalog = await self.get_catalog_entry(strategy_id)
        versions = await self._version_repo.get_by_catalog_id(catalog.catalog_id)
        deployments = await self._deployment_repo.get_by_catalog_id(catalog.catalog_id)

        return {
            "catalog": catalog.model_dump(),
            "versions": [v.model_dump() for v in versions],
            "deployments": [d.model_dump() for d in deployments],
        }

    # =========================================================================
    # EXPERIMENTS
    # =========================================================================

    async def create_experiment(
        self,
        strategy_id: str,
        name: str,
        parameter_set: dict,
        description: str = "",
        hypothesis: str = "",
    ) -> StrategyExperiment:
        catalog = await self.get_catalog_entry(strategy_id)

        experiment = StrategyExperiment(
            strategy_id=strategy_id,
            catalog_id=catalog.catalog_id,
            name=name,
            description=description,
            parameter_set=parameter_set,
            hypothesis=hypothesis,
            status=ExperimentStatus.PENDING,
        )
        saved = await self._experiment_repo.create(experiment)
        logger.info(
            "Created experiment experiment_id=%s strategy_id=%s",
            saved.experiment_id,
            strategy_id,
        )
        return saved

    async def list_experiments(
        self, strategy_id: str | None = None
    ) -> list[StrategyExperiment]:
        if strategy_id is not None:
            return await self._experiment_repo.get_by_strategy_id(strategy_id, limit=100)
        return await self._experiment_repo.get_all(limit=100)

    async def get_experiment(self, experiment_id: str) -> StrategyExperiment:
        experiment = await self._experiment_repo.get_by_experiment_id(experiment_id)
        if experiment is None:
            raise ValueError(f"Experiment {experiment_id} not found")
        return experiment

    async def run_experiment(
        self,
        experiment_id: str,
        from_date: str,
        to_date: str,
        symbols: list[str] | None = None,
    ) -> StrategyExperiment:
        experiment = await self.get_experiment(experiment_id)

        experiment.status = ExperimentStatus.RUNNING
        experiment.started_at = _utcnow()
        await self._experiment_repo.save(experiment)

        # Full integration: call BacktestService.run_backtest() with experiment.parameter_set and link result
        experiment.backtest_run_id = "pending_execution"
        experiment.results = {
            "status": "queued",
            "from_date": from_date,
            "to_date": to_date,
        }
        experiment.status = ExperimentStatus.COMPLETED
        experiment.completed_at = _utcnow()
        await self._experiment_repo.save(experiment)

        logger.info(
            "Run experiment experiment_id=%s from_date=%s to_date=%s",
            experiment_id,
            from_date,
            to_date,
        )
        return experiment

    async def link_experiment_results(
        self,
        experiment_id: str,
        backtest_run_id: str,
        results: dict,
    ) -> StrategyExperiment:
        experiment = await self.get_experiment(experiment_id)
        experiment.backtest_run_id = backtest_run_id
        experiment.results = results
        experiment.status = ExperimentStatus.COMPLETED
        experiment.completed_at = _utcnow()
        saved = await self._experiment_repo.save(experiment)
        logger.info(
            "Linked backtest results experiment_id=%s backtest_run_id=%s",
            experiment_id,
            backtest_run_id,
        )
        return saved

    # =========================================================================
    # A/B TESTING
    # =========================================================================

    async def create_ab_test(
        self,
        name: str,
        strategy_a_id: str,
        strategy_b_id: str,
        strategy_a_params: dict,
        strategy_b_params: dict,
        from_date: datetime,
        to_date: datetime,
        initial_capital: float = 1_000_000.0,
        description: str = "",
    ) -> ABTest:
        ab_test = ABTest(
            name=name,
            description=description,
            strategy_a_id=strategy_a_id,
            strategy_b_id=strategy_b_id,
            strategy_a_params=strategy_a_params,
            strategy_b_params=strategy_b_params,
            from_date=from_date,
            to_date=to_date,
            initial_capital=initial_capital,
            status=ABTestStatus.PENDING,
        )
        saved = await self._ab_test_repo.create(ab_test)
        logger.info(
            "Created A/B test ab_test_id=%s strategy_a=%s strategy_b=%s",
            saved.ab_test_id,
            strategy_a_id,
            strategy_b_id,
        )
        return saved

    async def run_ab_test(self, ab_test_id: str) -> ABTest:
        ab_test = await self.get_ab_test(ab_test_id)

        ab_test.status = ABTestStatus.RUNNING
        await self._ab_test_repo.save(ab_test)

        # Full integration: run two BacktestService.run_backtest() calls with identical date/capital
        ab_test.results_a = {
            "strategy_id": ab_test.strategy_a_id,
            "status": "queued",
        }
        ab_test.results_b = {
            "strategy_id": ab_test.strategy_b_id,
            "status": "queued",
        }
        await self._ab_test_repo.save(ab_test)

        logger.info("Running A/B test ab_test_id=%s", ab_test_id)
        return ab_test

    async def complete_ab_test(
        self,
        ab_test_id: str,
        results_a: dict,
        results_b: dict,
    ) -> ABTest:
        ab_test = await self.get_ab_test(ab_test_id)

        sharpe_a = results_a.get("sharpe_ratio")
        sharpe_b = results_b.get("sharpe_ratio")

        if sharpe_a is not None and sharpe_b is not None:
            if sharpe_a > sharpe_b:
                winner = ab_test.strategy_a_id
                winner_reason = f"Higher Sharpe ratio: {sharpe_a:.4f} vs {sharpe_b:.4f}"
            elif sharpe_b > sharpe_a:
                winner = ab_test.strategy_b_id
                winner_reason = f"Higher Sharpe ratio: {sharpe_b:.4f} vs {sharpe_a:.4f}"
            else:
                winner = "TIE"
                winner_reason = f"Equal Sharpe ratios: {sharpe_a:.4f}"
        else:
            pnl_a = results_a.get("total_pnl")
            pnl_b = results_b.get("total_pnl")
            if pnl_a is not None and pnl_b is not None:
                if pnl_a > pnl_b:
                    winner = ab_test.strategy_a_id
                    winner_reason = f"Higher total PnL: {pnl_a:.2f} vs {pnl_b:.2f}"
                elif pnl_b > pnl_a:
                    winner = ab_test.strategy_b_id
                    winner_reason = f"Higher total PnL: {pnl_b:.2f} vs {pnl_a:.2f}"
                else:
                    winner = "TIE"
                    winner_reason = f"Equal total PnL: {pnl_a:.2f}"
            else:
                winner = "TIE"
                winner_reason = "Insufficient metrics to determine a winner"

        updated = await self._ab_test_repo.update_results(
            ab_test_id=ab_test_id,
            results_a=results_a,
            results_b=results_b,
            winner=winner,
            winner_reason=winner_reason,
        )
        if updated is None:
            raise ValueError(f"A/B test {ab_test_id} not found during result update")

        logger.info(
            "Completed A/B test ab_test_id=%s winner=%s reason=%s",
            ab_test_id,
            winner,
            winner_reason,
        )
        return updated

    async def get_ab_test(self, ab_test_id: str) -> ABTest:
        ab_test = await self._ab_test_repo.get_by_ab_test_id(ab_test_id)
        if ab_test is None:
            raise ValueError(f"A/B test {ab_test_id} not found")
        return ab_test

    # =========================================================================
    # SCORECARD
    # =========================================================================

    async def compute_scorecard(
        self,
        strategy_id: str,
        data_source: str = "BACKTEST",
        backtest_run_id: str | None = None,
        metrics: dict | None = None,
    ) -> StrategyScorecard:
        catalog = await self.get_catalog_entry(strategy_id)
        m = metrics or {}

        win_rate = m.get("win_rate")
        expectancy = m.get("expectancy")
        max_drawdown = m.get("max_drawdown")
        sharpe_ratio = m.get("sharpe_ratio")
        walk_forward_score = m.get("walk_forward_score")
        monte_carlo_score = m.get("monte_carlo_score")

        overall_score, score_breakdown = StrategyScorecard.compute_overall_score(
            win_rate=win_rate,
            expectancy=expectancy,
            max_drawdown=max_drawdown,
            sharpe_ratio=sharpe_ratio,
            walk_forward_score=walk_forward_score,
            monte_carlo_score=monte_carlo_score,
        )

        try:
            ds_enum = ScorecardDataSource(data_source)
        except ValueError:
            ds_enum = ScorecardDataSource.BACKTEST

        scorecard = StrategyScorecard(
            catalog_id=catalog.catalog_id,
            strategy_id=strategy_id,
            data_source=ds_enum,
            backtest_run_id=backtest_run_id,
            win_rate=win_rate,
            expectancy=expectancy,
            max_drawdown=max_drawdown,
            sharpe_ratio=sharpe_ratio,
            profit_factor=m.get("profit_factor"),
            total_trades=m.get("total_trades"),
            total_pnl=m.get("total_pnl"),
            walk_forward_score=walk_forward_score,
            monte_carlo_score=monte_carlo_score,
            overall_score=overall_score,
            score_breakdown=score_breakdown,
        )
        saved = await self._scorecard_repo.upsert_for_strategy(scorecard)
        logger.info(
            "Computed scorecard strategy_id=%s overall_score=%.2f",
            strategy_id,
            overall_score,
        )
        return saved

    async def get_scorecard(self, strategy_id: str) -> StrategyScorecard:
        scorecard = await self._scorecard_repo.get_latest_for_strategy(strategy_id)
        if scorecard is None:
            raise ValueError(f"No scorecard found for strategy {strategy_id}")
        return scorecard

    async def get_leaderboard(self, limit: int = 20) -> list[StrategyScorecard]:
        return await self._scorecard_repo.get_leaderboard(limit)
