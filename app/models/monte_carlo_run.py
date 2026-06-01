"""
MonteCarloRun — top-level document tracking a Monte Carlo simulation run.

One run can analyze one or more strategies.  Per-strategy (and combined
portfolio) results are stored in separate MonteCarloResult documents linked
by run_id.

Collection: monte_carlo_runs
"""

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MonteCarloRunStatus(str, Enum):
    PENDING   = "PENDING"
    RUNNING   = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED    = "FAILED"


class MonteCarloRun(Document):
    run_id:           str          = Field(default_factory=lambda: str(uuid.uuid4()))
    strategy_ids:     list[str]    = Field(default_factory=list)
    simulation_count: int          = Field(default=1000)
    status:           MonteCarloRunStatus = Field(default=MonteCarloRunStatus.PENDING)
    started_at:       Optional[datetime] = Field(default=None)
    completed_at:     Optional[datetime] = Field(default=None)
    configuration:    dict          = Field(default_factory=dict)
    error_message:    Optional[str] = Field(default=None)
    metadata:         dict          = Field(default_factory=dict)
    created_at:       datetime      = Field(default_factory=_utcnow)
    updated_at:       datetime      = Field(default_factory=_utcnow)

    class Settings:
        name = "monte_carlo_runs"
        indexes = [
            IndexModel([("run_id", ASCENDING)], unique=True, name="mc_run_id_unique"),
            IndexModel([("status", ASCENDING)]),
            IndexModel([("created_at", DESCENDING)]),
        ]

    def mark_running(self) -> None:
        self.status     = MonteCarloRunStatus.RUNNING
        self.started_at = _utcnow()
        self.updated_at = _utcnow()

    def mark_completed(self, summary_metadata: dict) -> None:
        self.status       = MonteCarloRunStatus.COMPLETED
        self.completed_at = _utcnow()
        self.metadata.update(summary_metadata)
        self.updated_at   = _utcnow()

    def mark_failed(self, error: str) -> None:
        self.status        = MonteCarloRunStatus.FAILED
        self.completed_at  = _utcnow()
        self.error_message = error
        self.updated_at    = _utcnow()
