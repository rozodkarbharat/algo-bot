"""
Comprehensive unit tests for the Strategy Research Lab.

Tests cover:
  - Catalog management (list, get, register, duplicate detection)
  - Promotion pipeline (DEVELOPMENT -> TESTING -> PAPER -> LIVE, RETIRED)
  - Strategy versioning (add, list, duplicate detection)
  - Experiment framework (create, run, get, list)
  - A/B testing (create, run, complete with winner determination, tie)
  - Scorecard computation, retrieval, and leaderboard
  - Scorecard math via compute_overall_score (pure, no DB)
  - VALID_TRANSITIONS coverage

Strategy:
  - All repos are replaced on the service instance after construction.
  - No real MongoDB connections; purely unit tests.
  - AsyncMock is used for all async repo methods.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.strategy_catalog import (
    StrategyCatalog,
    StrategyDeployment,
    StrategyStatus,
    StrategyVersion,
    VALID_TRANSITIONS,
)
from app.models.strategy_experiment import (
    ABTest,
    ABTestStatus,
    ExperimentStatus,
    StrategyExperiment,
)
from app.models.strategy_scorecard import ScorecardDataSource, StrategyScorecard
from app.services.strategy_lab_service import StrategyLabService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _make_catalog(
    strategy_id: str = "one_side_orb",
    status: StrategyStatus = StrategyStatus.DEVELOPMENT,
    catalog_id: str | None = None,
    current_version: str = "1.0.0",
) -> StrategyCatalog:
    return StrategyCatalog.model_construct(
        catalog_id=catalog_id or str(uuid.uuid4()),
        strategy_id=strategy_id,
        strategy_name="One Side ORB",
        current_version=current_version,
        status=status,
        description="Opening range breakout strategy",
        category="momentum",
        tags=["orb", "intraday"],
        created_at=_utcnow(),
        updated_at=_utcnow(),
        metadata={},
    )


def _make_version(
    catalog_id: str | None = None,
    strategy_id: str = "one_side_orb",
    version: str = "1.0.0",
) -> StrategyVersion:
    return StrategyVersion.model_construct(
        version_id=str(uuid.uuid4()),
        catalog_id=catalog_id or str(uuid.uuid4()),
        strategy_id=strategy_id,
        version=version,
        parameters={"stop_loss_pct": 0.5, "target_pct": 1.0},
        change_notes="Initial registration",
        created_by="system",
        created_at=_utcnow(),
    )


def _make_experiment(
    strategy_id: str = "one_side_orb",
    catalog_id: str | None = None,
    status: ExperimentStatus = ExperimentStatus.PENDING,
    experiment_id: str | None = None,
) -> StrategyExperiment:
    return StrategyExperiment.model_construct(
        experiment_id=experiment_id or str(uuid.uuid4()),
        strategy_id=strategy_id,
        catalog_id=catalog_id or str(uuid.uuid4()),
        name="Test Experiment",
        description="A test experiment",
        parameter_set={"stop_loss_pct": 0.5},
        hypothesis="Lower stop loss improves returns",
        status=status,
        backtest_run_id=None,
        results={},
        error_message=None,
        created_at=_utcnow(),
        started_at=None,
        completed_at=None,
        metadata={},
    )


def _make_ab_test(
    strategy_a_id: str = "one_side_orb",
    strategy_b_id: str = "orhv",
    status: ABTestStatus = ABTestStatus.PENDING,
    ab_test_id: str | None = None,
) -> ABTest:
    return ABTest.model_construct(
        ab_test_id=ab_test_id or str(uuid.uuid4()),
        name="ORB vs ORHV",
        description="Head-to-head comparison",
        strategy_a_id=strategy_a_id,
        strategy_b_id=strategy_b_id,
        strategy_a_params={},
        strategy_b_params={},
        from_date=_utcnow(),
        to_date=_utcnow(),
        initial_capital=1_000_000.0,
        status=status,
        backtest_run_id_a=None,
        backtest_run_id_b=None,
        results_a={},
        results_b={},
        winner=None,
        winner_reason="",
        created_at=_utcnow(),
        completed_at=None,
        metadata={},
    )


def _make_scorecard(
    strategy_id: str = "one_side_orb",
    catalog_id: str | None = None,
    overall_score: float = 72.5,
) -> StrategyScorecard:
    return StrategyScorecard.model_construct(
        scorecard_id=str(uuid.uuid4()),
        catalog_id=catalog_id or str(uuid.uuid4()),
        strategy_id=strategy_id,
        computed_at=_utcnow(),
        data_source=ScorecardDataSource.BACKTEST,
        backtest_run_id=None,
        period_from=None,
        period_to=None,
        win_rate=0.65,
        expectancy=350.0,
        max_drawdown=0.10,
        sharpe_ratio=1.8,
        profit_factor=1.5,
        total_trades=120,
        total_pnl=42000.0,
        walk_forward_score=0.72,
        monte_carlo_score=0.80,
        overall_score=overall_score,
        score_breakdown={},
        notes="",
        metadata={},
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_catalog_repo() -> MagicMock:
    repo = MagicMock()
    repo.list_all = AsyncMock(return_value=[])
    repo.get_by_status = AsyncMock(return_value=[])
    repo.get_by_strategy_id = AsyncMock(return_value=None)
    repo.get_by_catalog_id = AsyncMock(return_value=None)
    repo.create = AsyncMock(side_effect=lambda doc: doc)
    repo.save = AsyncMock(side_effect=lambda doc: doc)
    repo.update_status = AsyncMock(return_value=None)
    return repo


@pytest.fixture
def mock_version_repo() -> MagicMock:
    repo = MagicMock()
    repo.get_by_catalog_id = AsyncMock(return_value=[])
    repo.get_by_version = AsyncMock(return_value=None)
    repo.get_by_version_id = AsyncMock(return_value=None)
    repo.get_latest_for_catalog = AsyncMock(return_value=None)
    repo.create = AsyncMock(side_effect=lambda doc: doc)
    repo.save = AsyncMock(side_effect=lambda doc: doc)
    return repo


@pytest.fixture
def mock_deployment_repo() -> MagicMock:
    repo = MagicMock()
    repo.get_by_catalog_id = AsyncMock(return_value=[])
    repo.get_by_strategy_id = AsyncMock(return_value=[])
    repo.get_by_deployment_id = AsyncMock(return_value=None)
    repo.create = AsyncMock(side_effect=lambda doc: doc)
    repo.save = AsyncMock(side_effect=lambda doc: doc)
    return repo


@pytest.fixture
def mock_experiment_repo() -> MagicMock:
    repo = MagicMock()
    repo.get_by_experiment_id = AsyncMock(return_value=None)
    repo.get_by_strategy_id = AsyncMock(return_value=[])
    repo.get_by_catalog_id = AsyncMock(return_value=[])
    repo.get_by_status = AsyncMock(return_value=[])
    repo.get_all = AsyncMock(return_value=[])
    repo.create = AsyncMock(side_effect=lambda doc: doc)
    repo.save = AsyncMock(side_effect=lambda doc: doc)
    repo.update_status = AsyncMock(return_value=None)
    return repo


@pytest.fixture
def mock_ab_test_repo() -> MagicMock:
    repo = MagicMock()
    repo.get_by_ab_test_id = AsyncMock(return_value=None)
    repo.list_all = AsyncMock(return_value=[])
    repo.get_by_status = AsyncMock(return_value=[])
    repo.create = AsyncMock(side_effect=lambda doc: doc)
    repo.save = AsyncMock(side_effect=lambda doc: doc)
    repo.update_results = AsyncMock(return_value=None)
    return repo


@pytest.fixture
def mock_scorecard_repo() -> MagicMock:
    repo = MagicMock()
    repo.get_by_scorecard_id = AsyncMock(return_value=None)
    repo.get_latest_for_strategy = AsyncMock(return_value=None)
    repo.get_all_for_strategy = AsyncMock(return_value=[])
    repo.get_leaderboard = AsyncMock(return_value=[])
    repo.upsert_for_strategy = AsyncMock(side_effect=lambda doc: doc)
    repo.create = AsyncMock(side_effect=lambda doc: doc)
    repo.save = AsyncMock(side_effect=lambda doc: doc)
    return repo


@pytest.fixture
def sample_catalog() -> StrategyCatalog:
    return _make_catalog(
        strategy_id="one_side_orb",
        status=StrategyStatus.DEVELOPMENT,
        catalog_id="cat-001",
    )


@pytest.fixture
def sample_version(sample_catalog: StrategyCatalog) -> StrategyVersion:
    return _make_version(
        catalog_id=sample_catalog.catalog_id,
        strategy_id=sample_catalog.strategy_id,
        version="1.0.0",
    )


@pytest.fixture
def sample_scorecard(sample_catalog: StrategyCatalog) -> StrategyScorecard:
    return _make_scorecard(
        strategy_id=sample_catalog.strategy_id,
        catalog_id=sample_catalog.catalog_id,
        overall_score=72.5,
    )


@pytest.fixture
def service(
    mock_catalog_repo: MagicMock,
    mock_version_repo: MagicMock,
    mock_deployment_repo: MagicMock,
    mock_experiment_repo: MagicMock,
    mock_ab_test_repo: MagicMock,
    mock_scorecard_repo: MagicMock,
) -> StrategyLabService:
    svc = StrategyLabService()
    svc._catalog_repo = mock_catalog_repo
    svc._version_repo = mock_version_repo
    svc._deployment_repo = mock_deployment_repo
    svc._experiment_repo = mock_experiment_repo
    svc._ab_test_repo = mock_ab_test_repo
    svc._scorecard_repo = mock_scorecard_repo
    return svc


# ===========================================================================
# TestStrategyCatalog
# ===========================================================================


class TestStrategyCatalog:
    @pytest.mark.asyncio
    async def test_list_catalog_no_filter(
        self,
        service: StrategyLabService,
        mock_catalog_repo: MagicMock,
        sample_catalog: StrategyCatalog,
    ) -> None:
        mock_catalog_repo.list_all.return_value = [sample_catalog]
        result = await service.list_catalog()
        assert len(result) == 1
        assert result[0].strategy_id == "one_side_orb"
        mock_catalog_repo.list_all.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_list_catalog_with_status_filter(
        self,
        service: StrategyLabService,
        mock_catalog_repo: MagicMock,
        sample_catalog: StrategyCatalog,
    ) -> None:
        mock_catalog_repo.get_by_status.return_value = [sample_catalog]
        result = await service.list_catalog(status_filter=StrategyStatus.DEVELOPMENT)
        assert len(result) == 1
        mock_catalog_repo.get_by_status.assert_awaited_once_with(StrategyStatus.DEVELOPMENT)
        mock_catalog_repo.list_all.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_get_catalog_entry_found(
        self,
        service: StrategyLabService,
        mock_catalog_repo: MagicMock,
        sample_catalog: StrategyCatalog,
    ) -> None:
        mock_catalog_repo.get_by_strategy_id.return_value = sample_catalog
        result = await service.get_catalog_entry("one_side_orb")
        assert result.strategy_id == "one_side_orb"
        assert result.status == StrategyStatus.DEVELOPMENT

    @pytest.mark.asyncio
    async def test_get_catalog_entry_not_found(
        self,
        service: StrategyLabService,
        mock_catalog_repo: MagicMock,
    ) -> None:
        mock_catalog_repo.get_by_strategy_id.return_value = None
        with pytest.raises(ValueError, match="not found in catalog"):
            await service.get_catalog_entry("nonexistent")

    @pytest.mark.asyncio
    async def test_register_strategy_success(
        self,
        service: StrategyLabService,
        mock_catalog_repo: MagicMock,
        mock_version_repo: MagicMock,
        sample_catalog: StrategyCatalog,
    ) -> None:
        mock_catalog_repo.get_by_strategy_id.return_value = None
        mock_catalog_repo.create.return_value = sample_catalog

        mock_metadata = MagicMock()
        mock_metadata.strategy_name = "One Side ORB"
        mock_metadata.description = "ORB strategy"
        mock_metadata.category = "momentum"
        mock_metadata.version = "1.0.0"
        mock_metadata.parameters = {}

        mock_strategy = MagicMock()
        mock_strategy.get_metadata.return_value = mock_metadata
        mock_strategy.get_default_config.return_value = {"stop_loss_pct": 0.5}

        with patch("app.strategy.strategy_registry.registry") as mock_registry:
            mock_registry.get.return_value = mock_strategy
            result = await service.register_strategy(
                strategy_id="one_side_orb",
                description="Custom description",
                category="momentum",
                tags=["orb"],
            )

        assert result.strategy_id == "one_side_orb"
        mock_catalog_repo.create.assert_awaited_once()
        mock_version_repo.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_register_strategy_duplicate(
        self,
        service: StrategyLabService,
        mock_catalog_repo: MagicMock,
        sample_catalog: StrategyCatalog,
    ) -> None:
        mock_catalog_repo.get_by_strategy_id.return_value = sample_catalog
        with pytest.raises(ValueError, match="already registered"):
            await service.register_strategy(strategy_id="one_side_orb")


# ===========================================================================
# TestPromotionPipeline
# ===========================================================================


class TestPromotionPipeline:
    @pytest.mark.asyncio
    async def test_promote_development_to_testing(
        self,
        service: StrategyLabService,
        mock_catalog_repo: MagicMock,
        mock_deployment_repo: MagicMock,
        sample_catalog: StrategyCatalog,
    ) -> None:
        # DEVELOPMENT -> TESTING (no pre-checks)
        catalog = _make_catalog(status=StrategyStatus.DEVELOPMENT)
        updated = _make_catalog(status=StrategyStatus.TESTING)
        mock_catalog_repo.get_by_strategy_id.return_value = catalog
        mock_catalog_repo.save.return_value = updated
        mock_deployment_repo.create.return_value = MagicMock()

        result = await service.promote_strategy("one_side_orb")
        assert result.status == StrategyStatus.TESTING
        mock_deployment_repo.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_promote_testing_to_paper(
        self,
        service: StrategyLabService,
        mock_catalog_repo: MagicMock,
        mock_deployment_repo: MagicMock,
        mock_experiment_repo: MagicMock,
    ) -> None:
        # TESTING -> PAPER requires at least one COMPLETED experiment
        catalog = _make_catalog(status=StrategyStatus.TESTING)
        updated = _make_catalog(status=StrategyStatus.PAPER)
        mock_catalog_repo.get_by_strategy_id.return_value = catalog
        mock_catalog_repo.save.return_value = updated

        completed_exp = _make_experiment(status=ExperimentStatus.COMPLETED)
        mock_experiment_repo.get_by_strategy_id.return_value = [completed_exp]
        mock_deployment_repo.create.return_value = MagicMock()

        result = await service.promote_strategy("one_side_orb")
        assert result.status == StrategyStatus.PAPER

    @pytest.mark.asyncio
    async def test_promote_paper_to_live_with_scorecard(
        self,
        service: StrategyLabService,
        mock_catalog_repo: MagicMock,
        mock_deployment_repo: MagicMock,
        mock_scorecard_repo: MagicMock,
        sample_scorecard: StrategyScorecard,
    ) -> None:
        # PAPER -> LIVE requires scorecard with overall_score >= 50
        catalog = _make_catalog(status=StrategyStatus.PAPER)
        updated = _make_catalog(status=StrategyStatus.LIVE)
        mock_catalog_repo.get_by_strategy_id.return_value = catalog
        mock_catalog_repo.save.return_value = updated
        mock_scorecard_repo.get_latest_for_strategy.return_value = sample_scorecard  # score=72.5
        mock_deployment_repo.create.return_value = MagicMock()

        result = await service.promote_strategy("one_side_orb")
        assert result.status == StrategyStatus.LIVE

    @pytest.mark.asyncio
    async def test_promote_paper_to_live_no_scorecard(
        self,
        service: StrategyLabService,
        mock_catalog_repo: MagicMock,
        mock_scorecard_repo: MagicMock,
    ) -> None:
        catalog = _make_catalog(status=StrategyStatus.PAPER)
        mock_catalog_repo.get_by_strategy_id.return_value = catalog
        mock_scorecard_repo.get_latest_for_strategy.return_value = None

        with pytest.raises(ValueError, match="no scorecard found"):
            await service.promote_strategy("one_side_orb")

    @pytest.mark.asyncio
    async def test_promote_paper_to_live_low_score(
        self,
        service: StrategyLabService,
        mock_catalog_repo: MagicMock,
        mock_scorecard_repo: MagicMock,
    ) -> None:
        catalog = _make_catalog(status=StrategyStatus.PAPER)
        low_score_card = _make_scorecard(overall_score=30.0)
        mock_catalog_repo.get_by_strategy_id.return_value = catalog
        mock_scorecard_repo.get_latest_for_strategy.return_value = low_score_card

        with pytest.raises(ValueError, match="below the required 50.0"):
            await service.promote_strategy("one_side_orb")

    @pytest.mark.asyncio
    async def test_promote_retired_raises(
        self,
        service: StrategyLabService,
        mock_catalog_repo: MagicMock,
    ) -> None:
        catalog = _make_catalog(status=StrategyStatus.RETIRED)
        mock_catalog_repo.get_by_strategy_id.return_value = catalog

        with pytest.raises(ValueError, match="already RETIRED"):
            await service.promote_strategy("one_side_orb")

    @pytest.mark.asyncio
    async def test_retire_from_any_status(
        self,
        service: StrategyLabService,
        mock_catalog_repo: MagicMock,
        mock_deployment_repo: MagicMock,
    ) -> None:
        catalog = _make_catalog(status=StrategyStatus.DEVELOPMENT)
        retired = _make_catalog(status=StrategyStatus.RETIRED)
        mock_catalog_repo.get_by_strategy_id.return_value = catalog
        mock_catalog_repo.save.return_value = retired
        mock_deployment_repo.create.return_value = MagicMock()

        result = await service.retire_strategy("one_side_orb")
        assert result.status == StrategyStatus.RETIRED
        mock_deployment_repo.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_promote_to_paper_no_experiments(
        self,
        service: StrategyLabService,
        mock_catalog_repo: MagicMock,
        mock_experiment_repo: MagicMock,
    ) -> None:
        # TESTING -> PAPER but zero completed experiments
        catalog = _make_catalog(status=StrategyStatus.TESTING)
        mock_catalog_repo.get_by_strategy_id.return_value = catalog
        mock_experiment_repo.get_by_strategy_id.return_value = []  # no experiments

        with pytest.raises(ValueError, match="COMPLETED experiment is required"):
            await service.promote_strategy("one_side_orb")


# ===========================================================================
# TestStrategyVersioning
# ===========================================================================


class TestStrategyVersioning:
    @pytest.mark.asyncio
    async def test_add_version_success(
        self,
        service: StrategyLabService,
        mock_catalog_repo: MagicMock,
        mock_version_repo: MagicMock,
        sample_catalog: StrategyCatalog,
    ) -> None:
        mock_catalog_repo.get_by_strategy_id.return_value = sample_catalog
        mock_version_repo.get_by_version.return_value = None  # version does not exist yet

        new_version = _make_version(
            catalog_id=sample_catalog.catalog_id,
            strategy_id=sample_catalog.strategy_id,
            version="2.0.0",
        )
        mock_version_repo.create.return_value = new_version
        mock_catalog_repo.save.return_value = sample_catalog

        result = await service.add_version(
            strategy_id="one_side_orb",
            version="2.0.0",
            parameters={"stop_loss_pct": 0.6},
            change_notes="Updated stop loss",
        )

        assert result.version == "2.0.0"
        mock_version_repo.create.assert_awaited_once()
        mock_catalog_repo.save.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_add_version_duplicate_raises(
        self,
        service: StrategyLabService,
        mock_catalog_repo: MagicMock,
        mock_version_repo: MagicMock,
        sample_catalog: StrategyCatalog,
        sample_version: StrategyVersion,
    ) -> None:
        mock_catalog_repo.get_by_strategy_id.return_value = sample_catalog
        mock_version_repo.get_by_version.return_value = sample_version  # already exists

        with pytest.raises(ValueError, match="already exists for strategy"):
            await service.add_version(
                strategy_id="one_side_orb",
                version="1.0.0",
                parameters={},
            )

    @pytest.mark.asyncio
    async def test_list_versions(
        self,
        service: StrategyLabService,
        mock_catalog_repo: MagicMock,
        mock_version_repo: MagicMock,
        sample_catalog: StrategyCatalog,
        sample_version: StrategyVersion,
    ) -> None:
        mock_catalog_repo.get_by_strategy_id.return_value = sample_catalog
        mock_version_repo.get_by_catalog_id.return_value = [sample_version]

        result = await service.list_versions("one_side_orb")
        assert len(result) == 1
        assert result[0].version == "1.0.0"
        mock_version_repo.get_by_catalog_id.assert_awaited_once_with(sample_catalog.catalog_id)


# ===========================================================================
# TestExperimentFramework
# ===========================================================================


class TestExperimentFramework:
    @pytest.mark.asyncio
    async def test_create_experiment(
        self,
        service: StrategyLabService,
        mock_catalog_repo: MagicMock,
        mock_experiment_repo: MagicMock,
        sample_catalog: StrategyCatalog,
    ) -> None:
        mock_catalog_repo.get_by_strategy_id.return_value = sample_catalog

        pending_exp = _make_experiment(
            strategy_id="one_side_orb",
            catalog_id=sample_catalog.catalog_id,
            status=ExperimentStatus.PENDING,
        )
        mock_experiment_repo.create.return_value = pending_exp

        result = await service.create_experiment(
            strategy_id="one_side_orb",
            name="Test Experiment",
            parameter_set={"stop_loss_pct": 0.5},
            description="Testing stop loss",
            hypothesis="Lower stop loss improves returns",
        )

        assert result.status == ExperimentStatus.PENDING
        assert result.strategy_id == "one_side_orb"
        mock_experiment_repo.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_experiment_found(
        self,
        service: StrategyLabService,
        mock_experiment_repo: MagicMock,
    ) -> None:
        exp = _make_experiment(experiment_id="exp-001")
        mock_experiment_repo.get_by_experiment_id.return_value = exp

        result = await service.get_experiment("exp-001")
        assert result.experiment_id == "exp-001"

    @pytest.mark.asyncio
    async def test_get_experiment_not_found(
        self,
        service: StrategyLabService,
        mock_experiment_repo: MagicMock,
    ) -> None:
        mock_experiment_repo.get_by_experiment_id.return_value = None

        with pytest.raises(ValueError, match="Experiment .* not found"):
            await service.get_experiment("nonexistent-exp")

    @pytest.mark.asyncio
    async def test_run_experiment(
        self,
        service: StrategyLabService,
        mock_experiment_repo: MagicMock,
    ) -> None:
        # run_experiment is a stub; it immediately marks the experiment as COMPLETED
        exp = _make_experiment(experiment_id="exp-001", status=ExperimentStatus.PENDING)
        mock_experiment_repo.get_by_experiment_id.return_value = exp
        mock_experiment_repo.save.return_value = exp

        result = await service.run_experiment(
            experiment_id="exp-001",
            from_date="2024-01-01",
            to_date="2024-03-31",
        )

        assert result.status == ExperimentStatus.COMPLETED
        assert result.backtest_run_id == "pending_execution"

    @pytest.mark.asyncio
    async def test_list_experiments_by_strategy(
        self,
        service: StrategyLabService,
        mock_experiment_repo: MagicMock,
    ) -> None:
        exp1 = _make_experiment(strategy_id="one_side_orb")
        exp2 = _make_experiment(strategy_id="one_side_orb")
        mock_experiment_repo.get_by_strategy_id.return_value = [exp1, exp2]

        result = await service.list_experiments(strategy_id="one_side_orb")
        assert len(result) == 2
        mock_experiment_repo.get_by_strategy_id.assert_awaited_once_with(
            "one_side_orb", limit=100
        )


# ===========================================================================
# TestABTesting
# ===========================================================================


class TestABTesting:
    @pytest.mark.asyncio
    async def test_create_ab_test(
        self,
        service: StrategyLabService,
        mock_ab_test_repo: MagicMock,
    ) -> None:
        ab_test = _make_ab_test(
            strategy_a_id="one_side_orb",
            strategy_b_id="orhv",
            status=ABTestStatus.PENDING,
        )
        mock_ab_test_repo.create.return_value = ab_test

        result = await service.create_ab_test(
            name="ORB vs ORHV",
            strategy_a_id="one_side_orb",
            strategy_b_id="orhv",
            strategy_a_params={},
            strategy_b_params={},
            from_date=_utcnow(),
            to_date=_utcnow(),
        )

        assert result.status == ABTestStatus.PENDING
        assert result.strategy_a_id == "one_side_orb"
        assert result.strategy_b_id == "orhv"
        mock_ab_test_repo.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_ab_test(
        self,
        service: StrategyLabService,
        mock_ab_test_repo: MagicMock,
    ) -> None:
        ab_test = _make_ab_test(ab_test_id="ab-001", status=ABTestStatus.PENDING)
        mock_ab_test_repo.get_by_ab_test_id.return_value = ab_test
        mock_ab_test_repo.save.return_value = ab_test

        result = await service.run_ab_test("ab-001")

        # After run_ab_test the status becomes RUNNING (stub sets results and saves twice)
        assert result.status == ABTestStatus.RUNNING
        assert mock_ab_test_repo.save.await_count == 2  # once for RUNNING, once for results

    @pytest.mark.asyncio
    async def test_complete_ab_test_with_winner(
        self,
        service: StrategyLabService,
        mock_ab_test_repo: MagicMock,
    ) -> None:
        ab_test = _make_ab_test(
            ab_test_id="ab-001",
            strategy_a_id="strategy_alpha",
            strategy_b_id="strategy_beta",
            status=ABTestStatus.RUNNING,
        )
        mock_ab_test_repo.get_by_ab_test_id.return_value = ab_test

        # Strategy A wins with higher sharpe
        completed = _make_ab_test(
            ab_test_id="ab-001",
            strategy_a_id="strategy_alpha",
            strategy_b_id="strategy_beta",
            status=ABTestStatus.COMPLETED,
        )
        completed.winner = "strategy_alpha"
        completed.winner_reason = "Higher Sharpe ratio"
        mock_ab_test_repo.update_results.return_value = completed

        result = await service.complete_ab_test(
            ab_test_id="ab-001",
            results_a={"sharpe_ratio": 2.5},
            results_b={"sharpe_ratio": 1.8},
        )

        assert result.winner == "strategy_alpha"
        mock_ab_test_repo.update_results.assert_awaited_once()

        # Verify the call args: sharpe_a=2.5 > sharpe_b=1.8 => winner should be strategy_alpha
        call_kwargs = mock_ab_test_repo.update_results.call_args
        assert call_kwargs.kwargs["winner"] == "strategy_alpha"

    @pytest.mark.asyncio
    async def test_complete_ab_test_tie(
        self,
        service: StrategyLabService,
        mock_ab_test_repo: MagicMock,
    ) -> None:
        ab_test = _make_ab_test(ab_test_id="ab-002", status=ABTestStatus.RUNNING)
        mock_ab_test_repo.get_by_ab_test_id.return_value = ab_test

        tied = _make_ab_test(ab_test_id="ab-002", status=ABTestStatus.COMPLETED)
        tied.winner = "TIE"
        tied.winner_reason = "Insufficient metrics to determine a winner"
        mock_ab_test_repo.update_results.return_value = tied

        # Empty results — no sharpe, no pnl => TIE
        result = await service.complete_ab_test(
            ab_test_id="ab-002",
            results_a={},
            results_b={},
        )

        assert result.winner == "TIE"
        call_kwargs = mock_ab_test_repo.update_results.call_args
        assert call_kwargs.kwargs["winner"] == "TIE"

    @pytest.mark.asyncio
    async def test_get_ab_test_not_found(
        self,
        service: StrategyLabService,
        mock_ab_test_repo: MagicMock,
    ) -> None:
        mock_ab_test_repo.get_by_ab_test_id.return_value = None

        with pytest.raises(ValueError, match="A/B test .* not found"):
            await service.get_ab_test("nonexistent-ab")


# ===========================================================================
# TestStrategyScorecard
# ===========================================================================


class TestStrategyScorecard:
    @pytest.mark.asyncio
    async def test_compute_scorecard_full_metrics(
        self,
        service: StrategyLabService,
        mock_catalog_repo: MagicMock,
        mock_scorecard_repo: MagicMock,
        sample_catalog: StrategyCatalog,
    ) -> None:
        mock_catalog_repo.get_by_strategy_id.return_value = sample_catalog

        metrics = {
            "win_rate": 0.65,
            "expectancy": 350.0,
            "max_drawdown": 0.10,
            "sharpe_ratio": 1.8,
            "walk_forward_score": 0.72,
            "monte_carlo_score": 0.80,
            "profit_factor": 1.5,
            "total_trades": 120,
            "total_pnl": 42000.0,
        }

        # Let upsert return whatever the service builds
        mock_scorecard_repo.upsert_for_strategy.side_effect = lambda doc: doc

        result = await service.compute_scorecard(
            strategy_id="one_side_orb",
            data_source="BACKTEST",
            metrics=metrics,
        )

        assert result.overall_score is not None
        assert 0.0 <= result.overall_score <= 100.0
        assert result.win_rate == 0.65
        assert result.sharpe_ratio == 1.8
        mock_scorecard_repo.upsert_for_strategy.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_compute_scorecard_partial_metrics(
        self,
        service: StrategyLabService,
        mock_catalog_repo: MagicMock,
        mock_scorecard_repo: MagicMock,
        sample_catalog: StrategyCatalog,
    ) -> None:
        mock_catalog_repo.get_by_strategy_id.return_value = sample_catalog
        mock_scorecard_repo.upsert_for_strategy.side_effect = lambda doc: doc

        # Only win_rate and sharpe provided; others default to 0 in compute_overall_score
        result = await service.compute_scorecard(
            strategy_id="one_side_orb",
            metrics={"win_rate": 0.60, "sharpe_ratio": 1.5},
        )

        assert result.overall_score is not None
        assert 0.0 <= result.overall_score <= 100.0
        assert result.expectancy is None
        assert result.max_drawdown is None

    @pytest.mark.asyncio
    async def test_get_scorecard_found(
        self,
        service: StrategyLabService,
        mock_scorecard_repo: MagicMock,
        sample_scorecard: StrategyScorecard,
    ) -> None:
        mock_scorecard_repo.get_latest_for_strategy.return_value = sample_scorecard

        result = await service.get_scorecard("one_side_orb")
        assert result.overall_score == 72.5
        mock_scorecard_repo.get_latest_for_strategy.assert_awaited_once_with("one_side_orb")

    @pytest.mark.asyncio
    async def test_get_scorecard_not_found(
        self,
        service: StrategyLabService,
        mock_scorecard_repo: MagicMock,
    ) -> None:
        mock_scorecard_repo.get_latest_for_strategy.return_value = None

        with pytest.raises(ValueError, match="No scorecard found for strategy"):
            await service.get_scorecard("one_side_orb")

    @pytest.mark.asyncio
    async def test_get_leaderboard(
        self,
        service: StrategyLabService,
        mock_scorecard_repo: MagicMock,
        sample_scorecard: StrategyScorecard,
    ) -> None:
        sc_a = _make_scorecard(strategy_id="strategy_a", overall_score=85.0)
        sc_b = _make_scorecard(strategy_id="strategy_b", overall_score=60.0)
        mock_scorecard_repo.get_leaderboard.return_value = [sc_a, sc_b]

        result = await service.get_leaderboard(limit=10)
        assert len(result) == 2
        assert result[0].overall_score == 85.0
        assert result[1].overall_score == 60.0
        mock_scorecard_repo.get_leaderboard.assert_awaited_once_with(10)


# ===========================================================================
# TestScorecardMath — pure unit tests, no DB required
# ===========================================================================


class TestScorecardMath:
    def test_perfect_score(self) -> None:
        """All inputs at their maximum values should yield a score close to 100."""
        score, breakdown = StrategyScorecard.compute_overall_score(
            win_rate=1.0,
            expectancy=1000.0,   # well above the 500 INR cap
            max_drawdown=0.0,    # no drawdown => full drawdown score
            sharpe_ratio=5.0,    # well above the 3.0 cap
            walk_forward_score=1.0,
            monte_carlo_score=1.0,
        )
        # With all inputs capped, the score should be very close to 100
        assert score == pytest.approx(100.0, abs=0.01)
        assert "overall_score" in breakdown

    def test_zero_score(self) -> None:
        """All inputs at zero should yield a score of 0 (except drawdown component)."""
        score, breakdown = StrategyScorecard.compute_overall_score(
            win_rate=0.0,
            expectancy=0.0,
            max_drawdown=0.0,    # max_drawdown=0 means best case => drawdown_score=20
            sharpe_ratio=0.0,
            walk_forward_score=0.0,
            monte_carlo_score=0.0,
        )
        # With max_drawdown=0, drawdown_score = (1-0)*100*0.20 = 20; everything else 0
        assert score == pytest.approx(20.0, abs=0.01)

    def test_truly_zero_score(self) -> None:
        """max_drawdown=1.0 (full ruin) with all other zeroes should give 0."""
        score, breakdown = StrategyScorecard.compute_overall_score(
            win_rate=0.0,
            expectancy=0.0,
            max_drawdown=1.0,   # full drawdown => drawdown_score = max(0, 0) = 0
            sharpe_ratio=0.0,
            walk_forward_score=0.0,
            monte_carlo_score=0.0,
        )
        assert score == pytest.approx(0.0, abs=0.01)

    def test_partial_score(self) -> None:
        """win_rate=0.6, sharpe=1.5 should produce a reasonable score in [0, 100]."""
        score, breakdown = StrategyScorecard.compute_overall_score(
            win_rate=0.6,
            expectancy=None,
            max_drawdown=None,
            sharpe_ratio=1.5,
            walk_forward_score=None,
            monte_carlo_score=None,
        )
        assert 0.0 <= score <= 100.0
        # win_rate contribution: 0.6*100*0.20 = 12.0
        # sharpe contribution: (1.5/3.0)*100*0.20 = 10.0
        # drawdown contribution with None (treated as 0): (1-0)*100*0.20 = 20.0
        # total floor: 12 + 0 + 20 + 10 + 0 + 0 = 42.0
        assert score >= 12.0
        assert score <= 100.0

    def test_none_inputs_treated_as_zero(self) -> None:
        """None inputs should behave identically to their zero counterparts."""
        score_none, _ = StrategyScorecard.compute_overall_score(
            win_rate=None,
            expectancy=None,
            max_drawdown=None,
            sharpe_ratio=None,
            walk_forward_score=None,
            monte_carlo_score=None,
        )
        score_zero, _ = StrategyScorecard.compute_overall_score(
            win_rate=0.0,
            expectancy=0.0,
            max_drawdown=0.0,
            sharpe_ratio=0.0,
            walk_forward_score=0.0,
            monte_carlo_score=0.0,
        )
        assert score_none == score_zero

    def test_score_breakdown_keys(self) -> None:
        """score_breakdown must contain all expected keys."""
        _, breakdown = StrategyScorecard.compute_overall_score(
            win_rate=0.5,
            expectancy=200.0,
            max_drawdown=0.05,
            sharpe_ratio=1.2,
            walk_forward_score=0.6,
            monte_carlo_score=0.7,
        )
        expected_keys = {
            "win_rate",
            "expectancy",
            "max_drawdown",
            "sharpe_ratio",
            "walk_forward_score",
            "monte_carlo_score",
            "overall_score",
        }
        assert expected_keys.issubset(breakdown.keys())

    def test_expectancy_cap(self) -> None:
        """Expectancy contribution must not exceed 15.0 (its max weighted value)."""
        _, breakdown = StrategyScorecard.compute_overall_score(
            win_rate=0.0,
            expectancy=100_000.0,   # extremely large
            max_drawdown=1.0,
            sharpe_ratio=0.0,
            walk_forward_score=0.0,
            monte_carlo_score=0.0,
        )
        expectancy_component = breakdown["expectancy"]["component_score"]
        assert expectancy_component <= 15.0 + 1e-9


# ===========================================================================
# TestValidTransitions
# ===========================================================================


class TestValidTransitions:
    def test_development_can_go_to_testing(self) -> None:
        transitions = VALID_TRANSITIONS[StrategyStatus.DEVELOPMENT]
        assert StrategyStatus.TESTING in transitions

    def test_development_can_also_retire(self) -> None:
        transitions = VALID_TRANSITIONS[StrategyStatus.DEVELOPMENT]
        assert StrategyStatus.RETIRED in transitions

    def test_live_can_only_retire(self) -> None:
        transitions = VALID_TRANSITIONS[StrategyStatus.LIVE]
        assert transitions == [StrategyStatus.RETIRED]

    def test_retired_has_no_transitions(self) -> None:
        transitions = VALID_TRANSITIONS[StrategyStatus.RETIRED]
        assert transitions == []

    def test_paper_can_go_to_live_or_testing_or_retired(self) -> None:
        transitions = VALID_TRANSITIONS[StrategyStatus.PAPER]
        assert StrategyStatus.LIVE in transitions
        assert StrategyStatus.TESTING in transitions
        assert StrategyStatus.RETIRED in transitions

    def test_testing_can_go_back_to_development(self) -> None:
        transitions = VALID_TRANSITIONS[StrategyStatus.TESTING]
        assert StrategyStatus.DEVELOPMENT in transitions

    def test_testing_can_go_to_paper(self) -> None:
        transitions = VALID_TRANSITIONS[StrategyStatus.TESTING]
        assert StrategyStatus.PAPER in transitions

    def test_all_statuses_have_transition_entries(self) -> None:
        """Every StrategyStatus must have an explicit entry in VALID_TRANSITIONS."""
        for status in StrategyStatus:
            assert status in VALID_TRANSITIONS, f"Missing transitions for {status}"

    def test_non_retired_forward_path_from_development(self) -> None:
        """The service's promote_strategy picks the first non-RETIRED transition."""
        valid = VALID_TRANSITIONS[StrategyStatus.DEVELOPMENT]
        non_retired = [s for s in valid if s != StrategyStatus.RETIRED]
        assert non_retired[0] == StrategyStatus.TESTING

    def test_non_retired_forward_path_from_testing(self) -> None:
        valid = VALID_TRANSITIONS[StrategyStatus.TESTING]
        non_retired = [s for s in valid if s != StrategyStatus.RETIRED]
        assert StrategyStatus.PAPER in non_retired

    def test_non_retired_forward_path_from_paper(self) -> None:
        valid = VALID_TRANSITIONS[StrategyStatus.PAPER]
        non_retired = [s for s in valid if s != StrategyStatus.RETIRED]
        assert non_retired[0] == StrategyStatus.LIVE
