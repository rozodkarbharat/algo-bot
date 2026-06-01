from datetime import datetime

from pydantic import BaseModel, Field

from app.models.strategy_catalog import StrategyStatus
from app.models.strategy_experiment import ABTestStatus, ExperimentStatus

# ---------------------------------------------------------------------------
# CATALOG
# ---------------------------------------------------------------------------


class RegisterStrategyRequest(BaseModel):
    strategy_id: str
    description: str = ""
    category: str = ""
    tags: list[str] = []


class StrategyVersionOut(BaseModel):
    version_id: str
    catalog_id: str
    strategy_id: str
    version: str
    parameters: dict
    change_notes: str
    created_by: str
    created_at: datetime


class StrategyDeploymentOut(BaseModel):
    deployment_id: str
    catalog_id: str
    strategy_id: str
    from_status: str | None
    to_status: str
    version: str
    approved_by: str
    notes: str
    deployed_at: datetime


class StrategyCatalogOut(BaseModel):
    catalog_id: str
    strategy_id: str
    strategy_name: str
    current_version: str
    status: str
    description: str
    category: str
    tags: list[str]
    created_at: datetime
    updated_at: datetime


class AddVersionRequest(BaseModel):
    version: str
    parameters: dict
    change_notes: str = ""
    created_by: str = "system"


# ---------------------------------------------------------------------------
# PROMOTION
# ---------------------------------------------------------------------------


class PromoteStrategyRequest(BaseModel):
    strategy_id: str
    approved_by: str = "system"
    notes: str = ""


class RetireStrategyRequest(BaseModel):
    strategy_id: str
    approved_by: str = "system"
    notes: str = ""


# ---------------------------------------------------------------------------
# EXPERIMENTS
# ---------------------------------------------------------------------------


class CreateExperimentRequest(BaseModel):
    strategy_id: str
    name: str
    parameter_set: dict
    description: str = ""
    hypothesis: str = ""


class RunExperimentRequest(BaseModel):
    from_date: str = Field(..., description="Format: YYYY-MM-DD")
    to_date: str = Field(..., description="Format: YYYY-MM-DD")
    symbols: list[str] = []


class ExperimentOut(BaseModel):
    experiment_id: str
    strategy_id: str
    catalog_id: str
    name: str
    description: str
    parameter_set: dict
    hypothesis: str
    status: str
    backtest_run_id: str | None
    results: dict
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


# ---------------------------------------------------------------------------
# A/B TESTING
# ---------------------------------------------------------------------------


class CreateABTestRequest(BaseModel):
    name: str
    strategy_a_id: str
    strategy_b_id: str
    strategy_a_params: dict
    strategy_b_params: dict
    from_date: datetime
    to_date: datetime
    initial_capital: float = 1_000_000.0
    description: str = ""


class CompleteABTestRequest(BaseModel):
    results_a: dict
    results_b: dict


class ABTestOut(BaseModel):
    ab_test_id: str
    name: str
    description: str
    strategy_a_id: str
    strategy_b_id: str
    strategy_a_params: dict
    strategy_b_params: dict
    from_date: datetime
    to_date: datetime
    initial_capital: float
    status: str
    backtest_run_id_a: str | None
    backtest_run_id_b: str | None
    results_a: dict
    results_b: dict
    winner: str | None
    winner_reason: str
    created_at: datetime
    completed_at: datetime | None


# ---------------------------------------------------------------------------
# SCORECARD
# ---------------------------------------------------------------------------


class ComputeScorecardRequest(BaseModel):
    strategy_id: str
    data_source: str = "BACKTEST"
    backtest_run_id: str | None = None
    metrics: dict = Field(
        default_factory=dict,
        description=(
            "Keys: win_rate, expectancy, max_drawdown, sharpe_ratio, "
            "walk_forward_score, monte_carlo_score"
        ),
    )


class ScorecardOut(BaseModel):
    scorecard_id: str
    strategy_id: str
    catalog_id: str
    computed_at: datetime
    data_source: str
    backtest_run_id: str | None
    period_from: datetime | None
    period_to: datetime | None
    win_rate: float | None
    expectancy: float | None
    max_drawdown: float | None
    sharpe_ratio: float | None
    profit_factor: float | None
    total_trades: int | None
    total_pnl: float | None
    walk_forward_score: float | None
    monte_carlo_score: float | None
    overall_score: float | None
    score_breakdown: dict
    notes: str


# ---------------------------------------------------------------------------
# DASHBOARD SUPPORT
# ---------------------------------------------------------------------------


class LeaderboardEntry(BaseModel):
    strategy_id: str
    strategy_name: str
    status: str
    overall_score: float | None
    win_rate: float | None
    sharpe_ratio: float | None
    max_drawdown: float | None
    computed_at: datetime


class LifecycleView(BaseModel):
    catalog: StrategyCatalogOut
    versions: list[StrategyVersionOut]
    deployments: list[StrategyDeploymentOut]
