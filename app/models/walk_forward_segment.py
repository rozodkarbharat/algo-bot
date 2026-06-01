import uuid
from datetime import datetime, timezone
from typing import Optional
from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class WalkForwardSegment(Document):
    segment_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str
    segment_number: int
    training_start: datetime
    training_end: datetime
    testing_start: datetime
    testing_end: datetime
    selected_parameters: dict = Field(default_factory=dict)
    optimization_score: float = Field(default=0.0)
    metrics: dict = Field(default_factory=dict)
    status: str = Field(default="pending")
    error_message: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "walk_forward_segments"
        indexes = [
            IndexModel([("segment_id", ASCENDING)], unique=True, name="walk_forward_segment_id_unique"),
            IndexModel([("run_id", ASCENDING)]),
            IndexModel([("run_id", ASCENDING), ("segment_number", ASCENDING)], unique=True, name="walk_forward_run_segment_unique"),
            IndexModel([("run_id", ASCENDING), ("status", ASCENDING)]),
        ]
