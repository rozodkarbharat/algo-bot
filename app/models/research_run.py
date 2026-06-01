"""
ResearchRun — one document per research / parameter-optimization run.

Tracks lifecycle, configuration, and completion status for a full
research cycle. Individual optimization results are stored separately
in parameter_optimization_results, linked by run_id.
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


class ResearchRunStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ResearchRun(Document):
    """
    Metadata and status for a single research/optimization run.

    Collection: research_runs
    Unique constraint: run_id
    """

    run_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique external identifier (UUID)",
    )

    # ── Multi-strategy fields ─────────────────────────────────────────────────
    strategy_id: str = Field(
        default="one_side_orb",
        description="Strategy this research run optimises",
    )
    strategy_name: str = Field(
        default="One-Side ORB",
        description="Human-readable strategy name",
    )

    started_at: Optional[datetime] = Field(default=None)
    completed_at: Optional[datetime] = Field(default=None)

    # Full configuration snapshot for reproducibility
    configuration: dict = Field(
        default_factory=dict,
        description="ResearchConfig serialised to dict at run time",
    )

    status: ResearchRunStatus = Field(default=ResearchRunStatus.PENDING)
    error_message: Optional[str] = Field(default=None)

    # Summary populated at completion
    metadata: dict = Field(
        default_factory=dict,
        description="Counts, top results, and other completion metadata",
    )

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "research_runs"
        indexes = [
            IndexModel([("run_id", ASCENDING)], unique=True, name="research_run_id_unique"),
            IndexModel([("status", ASCENDING)]),
            IndexModel([("strategy_id", ASCENDING)]),
            IndexModel([("created_at", DESCENDING)]),
        ]

    # ── State transitions ─────────────────────────────────────────────────────

    def mark_running(self) -> None:
        self.status = ResearchRunStatus.RUNNING
        self.started_at = _utcnow()
        self.updated_at = _utcnow()

    def mark_completed(self, summary_metadata: dict) -> None:
        self.status = ResearchRunStatus.COMPLETED
        self.completed_at = _utcnow()
        self.metadata.update(summary_metadata)
        self.updated_at = _utcnow()

    def mark_failed(self, error: str) -> None:
        self.status = ResearchRunStatus.FAILED
        self.completed_at = _utcnow()
        self.error_message = error
        self.updated_at = _utcnow()
