"""
Strategy Research Lab API routes.

GET  /catalog                         — list strategy catalog (filterable by status)
POST /catalog                         — register a new strategy
GET  /catalog/{strategy_id}           — get single catalog entry
GET  /lifecycle/{strategy_id}         — full lifecycle view (catalog + versions + deployments)
GET  /versions/{strategy_id}          — list versions for a strategy
POST /versions/{strategy_id}          — add a new version
POST /promote                         — promote strategy to next lifecycle stage
POST /retire                          — retire a strategy
GET  /scorecard                       — get latest scorecard for a strategy
POST /scorecard/compute               — compute and persist a new scorecard
GET  /leaderboard                     — ranked leaderboard by overall_score
POST /experiments                     — create experiment
GET  /experiments                     — list experiments (optionally filtered by strategy)
GET  /experiments/{experiment_id}     — get experiment detail
POST /experiments/{experiment_id}/run — trigger experiment execution
POST /ab-tests                        — create A/B test
GET  /ab-tests/{ab_test_id}           — get A/B test detail
POST /ab-tests/{ab_test_id}/run       — run A/B test
POST /ab-tests/{ab_test_id}/complete  — submit A/B test results

Routes call StrategyLabService only — no direct repository or Beanie access.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.services.strategy_lab_service import StrategyLabService
from app.schemas.strategy_lab import (
    RegisterStrategyRequest,
    StrategyCatalogOut,
    AddVersionRequest,
    StrategyVersionOut,
    PromoteStrategyRequest,
    RetireStrategyRequest,
    CreateExperimentRequest,
    RunExperimentRequest,
    ExperimentOut,
    CreateABTestRequest,
    CompleteABTestRequest,
    ABTestOut,
    ComputeScorecardRequest,
    ScorecardOut,
    LeaderboardEntry,
    LifecycleView,
    StrategyDeploymentOut,
)
from app.utils.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)


def _raise_for_error(exc: Exception) -> None:
    """Convert ValueError to appropriate HTTPException based on message content."""
    msg = str(exc)
    if "not found" in msg.lower():
        raise HTTPException(status_code=404, detail=msg)
    raise HTTPException(status_code=400, detail=msg)


# ── GET /catalog ───────────────────────────────────────────────────────────────

@router.get(
    "/catalog",
    summary="List strategy catalog",
    description=(
        "Return all registered strategies, optionally filtered by lifecycle status "
        "(DEVELOPMENT, PAPER, LIVE, RETIRED)."
    ),
)
async def list_catalog(
    status: Optional[str] = Query(default=None, description="Filter by status"),
) -> list[dict]:
    try:
        svc = StrategyLabService()
        entries = await svc.list_catalog(status)
        return [e.model_dump() for e in entries]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ── POST /catalog ──────────────────────────────────────────────────────────────

@router.post(
    "/catalog",
    summary="Register a strategy in the catalog",
    description=(
        "Create a catalog entry for an existing strategy (must be present in the "
        "strategy registry). An initial version 1.0.0 is created automatically."
    ),
)
async def register_strategy(body: RegisterStrategyRequest) -> dict:
    try:
        svc = StrategyLabService()
        catalog = await svc.register_strategy(
            strategy_id=body.strategy_id,
            description=body.description,
            category=body.category,
            tags=body.tags,
        )
        return catalog.model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ── GET /catalog/{strategy_id} ────────────────────────────────────────────────

@router.get(
    "/catalog/{strategy_id}",
    summary="Get a single catalog entry",
)
async def get_catalog_entry(strategy_id: str) -> dict:
    try:
        svc = StrategyLabService()
        catalog = await svc.get_catalog_entry(strategy_id)
        return catalog.model_dump()
    except ValueError as exc:
        _raise_for_error(exc)


# ── GET /lifecycle/{strategy_id} ──────────────────────────────────────────────

@router.get(
    "/lifecycle/{strategy_id}",
    summary="Strategy lifecycle view",
    description="Return catalog entry, full version history, and deployment audit trail.",
)
async def get_lifecycle(strategy_id: str) -> dict:
    try:
        svc = StrategyLabService()
        return await svc.get_lifecycle(strategy_id)
    except ValueError as exc:
        _raise_for_error(exc)


# ── GET /versions/{strategy_id} ───────────────────────────────────────────────

@router.get(
    "/versions/{strategy_id}",
    summary="List versions for a strategy",
)
async def list_versions(strategy_id: str) -> list[dict]:
    try:
        svc = StrategyLabService()
        versions = await svc.list_versions(strategy_id)
        return [v.model_dump() for v in versions]
    except ValueError as exc:
        _raise_for_error(exc)


# ── POST /versions/{strategy_id} ──────────────────────────────────────────────

@router.post(
    "/versions/{strategy_id}",
    summary="Add a new version to a strategy",
)
async def add_version(strategy_id: str, body: AddVersionRequest) -> dict:
    try:
        svc = StrategyLabService()
        version = await svc.add_version(
            strategy_id=strategy_id,
            version=body.version,
            parameters=body.parameters,
            change_notes=body.change_notes,
            created_by=body.created_by,
        )
        return version.model_dump()
    except ValueError as exc:
        _raise_for_error(exc)


# ── POST /promote ──────────────────────────────────────────────────────────────

@router.post(
    "/promote",
    summary="Promote strategy to next lifecycle stage",
    description=(
        "Advance a strategy through the lifecycle: "
        "DEVELOPMENT → PAPER → LIVE. "
        "Promotion to PAPER requires at least one COMPLETED experiment; "
        "promotion to LIVE requires a scorecard with overall_score >= 50."
    ),
)
async def promote_strategy(body: PromoteStrategyRequest) -> dict:
    try:
        svc = StrategyLabService()
        catalog = await svc.promote_strategy(
            strategy_id=body.strategy_id,
            approved_by=body.approved_by,
            notes=body.notes,
        )
        return catalog.model_dump()
    except ValueError as exc:
        msg = str(exc)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)


# ── POST /retire ───────────────────────────────────────────────────────────────

@router.post(
    "/retire",
    summary="Retire a strategy",
    description="Move a strategy to RETIRED status. This transition is irreversible.",
)
async def retire_strategy(body: RetireStrategyRequest) -> dict:
    try:
        svc = StrategyLabService()
        catalog = await svc.retire_strategy(
            strategy_id=body.strategy_id,
            approved_by=body.approved_by,
            notes=body.notes,
        )
        return catalog.model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ── GET /scorecard ─────────────────────────────────────────────────────────────

@router.get(
    "/scorecard",
    summary="Get latest scorecard for a strategy",
)
async def get_scorecard(
    strategy_id: str = Query(..., description="Strategy ID"),
) -> dict:
    try:
        svc = StrategyLabService()
        scorecard = await svc.get_scorecard(strategy_id)
        return scorecard.model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ── POST /scorecard/compute ────────────────────────────────────────────────────

@router.post(
    "/scorecard/compute",
    summary="Compute and persist a scorecard for a strategy",
    description=(
        "Compute an overall score from provided metrics (win_rate, expectancy, "
        "max_drawdown, sharpe_ratio, walk_forward_score, monte_carlo_score) and "
        "persist the result. Overwrites any previous scorecard for the strategy."
    ),
)
async def compute_scorecard(body: ComputeScorecardRequest) -> dict:
    try:
        svc = StrategyLabService()
        scorecard = await svc.compute_scorecard(
            strategy_id=body.strategy_id,
            data_source=body.data_source,
            backtest_run_id=body.backtest_run_id,
            metrics=body.metrics,
        )
        return scorecard.model_dump()
    except ValueError as exc:
        _raise_for_error(exc)


# ── GET /leaderboard ───────────────────────────────────────────────────────────

@router.get(
    "/leaderboard",
    summary="Strategy leaderboard ranked by overall scorecard score",
    description=(
        "Returns strategies sorted by overall_score descending. "
        "Each entry merges scorecard metrics with catalog metadata."
    ),
)
async def get_leaderboard(
    limit: int = Query(default=20, ge=1, le=200, description="Max number of entries"),
) -> list[dict]:
    try:
        svc = StrategyLabService()
        scorecards = await svc.get_leaderboard(limit)

        entries: list[dict] = []
        for scorecard in scorecards:
            try:
                catalog = await svc.get_catalog_entry(scorecard.strategy_id)
                catalog_data = catalog.model_dump()
            except ValueError:
                catalog_data = {}

            entry = {
                **scorecard.model_dump(),
                "strategy_name": catalog_data.get("strategy_name"),
                "status": catalog_data.get("status"),
                "category": catalog_data.get("category"),
                "tags": catalog_data.get("tags", []),
            }
            entries.append(entry)

        return entries
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ── POST /experiments ──────────────────────────────────────────────────────────

@router.post(
    "/experiments",
    summary="Create a new experiment",
    description=(
        "Create an experiment to test a specific parameter configuration "
        "for a strategy. The experiment is created in PENDING status and "
        "must be triggered separately via /experiments/{id}/run."
    ),
)
async def create_experiment(body: CreateExperimentRequest) -> dict:
    try:
        svc = StrategyLabService()
        experiment = await svc.create_experiment(
            strategy_id=body.strategy_id,
            name=body.name,
            parameter_set=body.parameter_set,
            description=body.description,
            hypothesis=body.hypothesis,
        )
        return experiment.model_dump()
    except ValueError as exc:
        _raise_for_error(exc)


# ── GET /experiments ───────────────────────────────────────────────────────────

@router.get(
    "/experiments",
    summary="List experiments",
)
async def list_experiments(
    strategy_id: Optional[str] = Query(default=None, description="Filter by strategy ID"),
) -> list[dict]:
    try:
        svc = StrategyLabService()
        experiments = await svc.list_experiments(strategy_id)
        return [e.model_dump() for e in experiments]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ── GET /experiments/{experiment_id} ──────────────────────────────────────────

@router.get(
    "/experiments/{experiment_id}",
    summary="Get experiment detail",
)
async def get_experiment(experiment_id: str) -> dict:
    try:
        svc = StrategyLabService()
        experiment = await svc.get_experiment(experiment_id)
        return experiment.model_dump()
    except ValueError as exc:
        _raise_for_error(exc)


# ── POST /experiments/{experiment_id}/run ─────────────────────────────────────

@router.post(
    "/experiments/{experiment_id}/run",
    summary="Trigger experiment execution",
    description=(
        "Queue/execute a backtest run for the experiment's parameter set "
        "over the specified date range and symbols."
    ),
)
async def run_experiment(experiment_id: str, body: RunExperimentRequest) -> dict:
    try:
        svc = StrategyLabService()
        experiment = await svc.run_experiment(
            experiment_id=experiment_id,
            from_date=body.from_date,
            to_date=body.to_date,
            symbols=body.symbols,
        )
        return experiment.model_dump()
    except ValueError as exc:
        _raise_for_error(exc)


# ── POST /ab-tests ─────────────────────────────────────────────────────────────

@router.post(
    "/ab-tests",
    summary="Create a new A/B test",
    description=(
        "Set up a head-to-head comparison between two strategy configurations "
        "over the same date range and initial capital."
    ),
)
async def create_ab_test(body: CreateABTestRequest) -> dict:
    try:
        svc = StrategyLabService()
        ab_test = await svc.create_ab_test(
            name=body.name,
            strategy_a_id=body.strategy_a_id,
            strategy_b_id=body.strategy_b_id,
            strategy_a_params=body.strategy_a_params,
            strategy_b_params=body.strategy_b_params,
            from_date=body.from_date,
            to_date=body.to_date,
            initial_capital=body.initial_capital,
            description=body.description,
        )
        return ab_test.model_dump()
    except ValueError as exc:
        _raise_for_error(exc)


# ── GET /ab-tests/{ab_test_id} ────────────────────────────────────────────────

@router.get(
    "/ab-tests/{ab_test_id}",
    summary="Get A/B test detail",
)
async def get_ab_test(ab_test_id: str) -> dict:
    try:
        svc = StrategyLabService()
        ab_test = await svc.get_ab_test(ab_test_id)
        return ab_test.model_dump()
    except ValueError as exc:
        _raise_for_error(exc)


# ── POST /ab-tests/{ab_test_id}/run ───────────────────────────────────────────

@router.post(
    "/ab-tests/{ab_test_id}/run",
    summary="Run A/B test",
    description="Execute both strategy legs of the A/B test.",
)
async def run_ab_test(ab_test_id: str) -> dict:
    try:
        svc = StrategyLabService()
        ab_test = await svc.run_ab_test(ab_test_id)
        return ab_test.model_dump()
    except ValueError as exc:
        _raise_for_error(exc)


# ── POST /ab-tests/{ab_test_id}/complete ──────────────────────────────────────

@router.post(
    "/ab-tests/{ab_test_id}/complete",
    summary="Submit A/B test results and determine winner",
    description=(
        "Provide backtest results for both strategy legs. The service determines "
        "the winner based on Sharpe ratio (falling back to total PnL)."
    ),
)
async def complete_ab_test(ab_test_id: str, body: CompleteABTestRequest) -> dict:
    try:
        svc = StrategyLabService()
        ab_test = await svc.complete_ab_test(
            ab_test_id=ab_test_id,
            results_a=body.results_a,
            results_b=body.results_b,
        )
        return ab_test.model_dump()
    except ValueError as exc:
        _raise_for_error(exc)
