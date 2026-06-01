import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class WalkForwardRunStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class WalkForwardRun(Document):
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    strategy_id: str = Field(default="one_side_orb")
    strategy_name: str = Field(default="One-Side ORB")
    started_at: Optional[datetime] = Field(default=None)
    completed_at: Optional[datetime] = Field(default=None)
    status: WalkForwardRunStatus = Field(default=WalkForwardRunStatus.PENDING)
    configuration: dict = Field(default_factory=dict)
    error_message: Optional[str] = Field(default=None)
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "walk_forward_runs"
        indexes = [
            IndexModel([("run_id", ASCENDING)], unique=True, name="walk_forward_run_id_unique"),
            IndexModel([("status", ASCENDING)]),
            IndexModel([("strategy_id", ASCENDING)]),
            IndexModel([("created_at", DESCENDING)]),
        ]

    def mark_running(self) -> None:
        self.status = WalkForwardRunStatus.RUNNING
        self.started_at = _utcnow()
        self.updated_at = _utcnow()

    def mark_completed(self, summary_metadata: dict) -> None:
        self.status = WalkForwardRunStatus.COMPLETED
        self.completed_at = _utcnow()
        self.metadata.update(summary_metadata)
        self.updated_at = _utcnow()

    def mark_failed(self, error: str) -> None:
        self.status = WalkForwardRunStatus.FAILED
        self.completed_at = _utcnow()
        self.error_message = error
        self.updated_at = _utcnow()
