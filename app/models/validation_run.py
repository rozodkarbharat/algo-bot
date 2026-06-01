from datetime import datetime, timezone
from enum import StrEnum
from typing import Optional
from uuid import uuid4

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ValidationRunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ValidationRun(Document):
    run_id: str = Field(default_factory=lambda: uuid4().hex)
    strategy_id: str = Field(default="one_side_orb")
    strategy_name: str = Field(default="One-Side ORB")
    trading_date: Optional[datetime] = None  # UTC midnight; None = all-time run
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    status: ValidationRunStatus = Field(default=ValidationRunStatus.PENDING)
    signal_count: int = 0
    executed_count: int = 0
    missed_count: int = 0
    avg_entry_slippage_bps: float = 0.0
    avg_exit_slippage_bps: float = 0.0
    avg_signal_latency_ms: float = 0.0
    avg_execution_latency_ms: float = 0.0
    health_score: Optional[float] = None
    error_message: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "validation_runs"
        indexes = [
            IndexModel([("run_id", ASCENDING)], unique=True, name="validation_run_id_unique"),
            IndexModel([("strategy_id", ASCENDING)]),
            IndexModel([("status", ASCENDING)]),
            IndexModel([("created_at", DESCENDING)]),
            IndexModel([("trading_date", ASCENDING)]),
        ]

    def mark_running(self) -> None:
        self.status = ValidationRunStatus.RUNNING
        self.started_at = _utcnow()
        self.updated_at = _utcnow()

    def mark_completed(self) -> None:
        self.status = ValidationRunStatus.COMPLETED
        self.completed_at = _utcnow()
        self.updated_at = _utcnow()

    def mark_failed(self, error: str) -> None:
        self.status = ValidationRunStatus.FAILED
        self.completed_at = _utcnow()
        self.error_message = error
        self.updated_at = _utcnow()
