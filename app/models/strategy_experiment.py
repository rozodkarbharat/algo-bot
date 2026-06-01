"""
Strategy experimentation models: StrategyExperiment and ABTest.

StrategyExperiment tracks a single parameter-set trial against a known strategy,
linking to a BacktestRun once execution begins.

ABTest runs a head-to-head comparison between two strategies (or two parameter
configurations of the same strategy) over an identical date range and capital base,
then records the winner and the reason.
"""

import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Optional

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ExperimentStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ABTestStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# ---------------------------------------------------------------------------
# StrategyExperiment
# ---------------------------------------------------------------------------


class StrategyExperiment(Document):
    """One document per experiment run against a specific parameter configuration.

    An experiment is created before execution and transitions through
    PENDING -> RUNNING -> COMPLETED | FAILED.  Once the linked BacktestRun
    finishes, ``results`` is populated with the key metrics summary.
    """

    experiment_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    strategy_id: str
    catalog_id: str
    name: str
    description: str = Field(default="")
    parameter_set: dict = Field(default_factory=dict)
    hypothesis: str = Field(default="")
    status: ExperimentStatus = Field(default=ExperimentStatus.PENDING)

    # Linked backtest execution
    backtest_run_id: Optional[str] = None

    # Outcome
    results: dict = Field(default_factory=dict)
    error_message: Optional[str] = None

    # Timestamps
    created_at: datetime = Field(default_factory=_utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    metadata: dict = Field(default_factory=dict)

    class Settings:
        name = "strategy_experiments"
        indexes = [
            IndexModel(
                [("experiment_id", ASCENDING)],
                unique=True,
                name="experiment_id_unique",
            ),
            IndexModel([("strategy_id", ASCENDING)], name="experiment_strategy_id"),
            IndexModel([("catalog_id", ASCENDING)], name="experiment_catalog_id"),
            IndexModel([("status", ASCENDING)], name="experiment_status"),
            IndexModel([("created_at", DESCENDING)], name="experiment_created_at"),
        ]


# ---------------------------------------------------------------------------
# ABTest
# ---------------------------------------------------------------------------


class ABTest(Document):
    """A/B comparison between two strategies (or two parameter sets of the same strategy).

    Both legs are run over an identical date range with the same initial capital
    so that performance differences are attributable only to the strategy or
    parameter differences.  After completion, ``winner`` holds the winning
    strategy_id, or "TIE" when neither leg is clearly superior.
    """

    ab_test_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str = Field(default="")

    # Strategy legs
    strategy_a_id: str
    strategy_b_id: str
    strategy_a_params: dict = Field(default_factory=dict)
    strategy_b_params: dict = Field(default_factory=dict)

    # Shared test window and capital
    from_date: datetime
    to_date: datetime
    initial_capital: float = Field(default=1_000_000.0)

    status: ABTestStatus = Field(default=ABTestStatus.PENDING)

    # Linked backtest executions
    backtest_run_id_a: Optional[str] = None
    backtest_run_id_b: Optional[str] = None

    # Outcomes per leg
    results_a: dict = Field(default_factory=dict)
    results_b: dict = Field(default_factory=dict)

    # Decision
    winner: Optional[str] = None          # strategy_id of winner, or "TIE"
    winner_reason: str = Field(default="")

    # Timestamps
    created_at: datetime = Field(default_factory=_utcnow)
    completed_at: Optional[datetime] = None

    metadata: dict = Field(default_factory=dict)

    class Settings:
        name = "ab_tests"
        indexes = [
            IndexModel(
                [("ab_test_id", ASCENDING)],
                unique=True,
                name="ab_test_id_unique",
            ),
            IndexModel([("status", ASCENDING)], name="ab_test_status"),
            IndexModel([("created_at", DESCENDING)], name="ab_test_created_at"),
        ]
