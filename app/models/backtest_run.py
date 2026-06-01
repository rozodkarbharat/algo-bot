"""
BacktestRun — one document per backtest execution.

Tracks lifecycle, configuration, and summary metrics for a full
historical backtest run. Individual trades are stored separately
in the backtest_trades collection, linked by run_id.
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


class BacktestRunStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class BacktestRun(Document):
    """
    Metadata and summary for a single backtest run.

    Collection: backtest_runs
    Unique constraint: run_id
    """

    run_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique external run identifier (UUID)",
    )
    strategy_id: str = Field(
        default="one_side_orb",
        description="Machine-readable strategy identifier",
    )
    strategy_name: str = Field(
        default="One-Side ORB",
        description="Human-readable strategy name at run time",
    )
    strategy_version: str = Field(
        default="1.0.0",
        description="Strategy version at run time (for reproducibility)",
    )

    # Execution timestamps
    started_at: Optional[datetime] = Field(default=None)
    completed_at: Optional[datetime] = Field(default=None)

    # Scope
    symbols: list[str] = Field(
        default_factory=list,
        description="Symbols included in this run (empty = all active at run time)",
    )
    backtest_from: Optional[datetime] = Field(
        default=None,
        description="Start date of the backtest range (UTC midnight)",
    )
    backtest_to: Optional[datetime] = Field(
        default=None,
        description="End date of the backtest range (UTC midnight)",
    )

    # Configuration snapshot — stores the BacktestConfig dict so results are reproducible
    configuration: dict = Field(
        default_factory=dict,
        description="Full configuration snapshot at run time",
    )

    # Populated after completion
    summary_metrics: dict = Field(
        default_factory=dict,
        description="High-level metrics snapshot (full metrics in BacktestMetrics collection)",
    )

    status: BacktestRunStatus = Field(default=BacktestRunStatus.PENDING)
    error_message: Optional[str] = Field(default=None)
    metadata: dict = Field(default_factory=dict)

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "backtest_runs"
        indexes = [
            IndexModel([("run_id", ASCENDING)], unique=True, name="run_id_unique"),
            IndexModel([("status", ASCENDING)]),
            IndexModel([("strategy_id", ASCENDING)]),
            IndexModel([("strategy_name", ASCENDING)]),
            IndexModel([("created_at", DESCENDING)]),
        ]

    # ── State transitions ─────────────────────────────────────────────────────

    def mark_running(self) -> None:
        self.status = BacktestRunStatus.RUNNING
        self.started_at = _utcnow()
        self.updated_at = _utcnow()

    def mark_completed(self, summary: dict) -> None:
        self.status = BacktestRunStatus.COMPLETED
        self.completed_at = _utcnow()
        self.summary_metrics = summary
        self.updated_at = _utcnow()

    def mark_failed(self, error: str) -> None:
        self.status = BacktestRunStatus.FAILED
        self.completed_at = _utcnow()
        self.error_message = error
        self.updated_at = _utcnow()
