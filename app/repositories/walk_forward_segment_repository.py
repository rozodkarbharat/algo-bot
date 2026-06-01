"""
Repository for WalkForwardSegment documents.

Follows the same raw-dict query pattern as every other repository in this
codebase — no ORM-style Beanie field expressions (Beanie 2.x / Pydantic v2).
Uses bulk_write for performance when saving large segment batches.
"""

from typing import Optional

from pymongo import InsertOne

from app.core.exceptions import DatabaseException
from app.models.walk_forward_segment import WalkForwardSegment
from app.repositories.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class WalkForwardSegmentRepository(BaseRepository[WalkForwardSegment]):
    document_model = WalkForwardSegment

    # ── Reads ─────────────────────────────────────────────────────────────────

    async def get_segments_for_run(self, run_id: str) -> list[WalkForwardSegment]:
        """Return all segments for a run, ordered by segment_number ascending."""
        return (
            await WalkForwardSegment.find({"run_id": run_id})
            .sort("segment_number")
            .to_list()
        )

    async def get_segment_by_id(self, segment_id: str) -> Optional[WalkForwardSegment]:
        """Return a WalkForwardSegment by its segment_id, or None if not found."""
        return await WalkForwardSegment.find_one({"segment_id": segment_id})

    async def count_completed(self, run_id: str) -> int:
        """Return the number of completed segments for a run."""
        return await WalkForwardSegment.find({"run_id": run_id, "status": "completed"}).count()

    # ── Writes ────────────────────────────────────────────────────────────────

    async def create_segment(self, segment: WalkForwardSegment) -> WalkForwardSegment:
        """Insert a new WalkForwardSegment and return the persisted instance."""
        return await self.create(segment)

    async def update_segment(self, segment: WalkForwardSegment) -> WalkForwardSegment:
        """Persist changes to an existing WalkForwardSegment."""
        return await self.save(segment)

    async def bulk_insert(self, segments: list[WalkForwardSegment]) -> int:
        """
        Insert many WalkForwardSegment documents in a single bulk_write call.

        Returns the count of inserted documents.
        Uses unordered InsertOne operations so a single failure does not abort
        the entire batch.
        """
        if not segments:
            return 0
        try:
            collection = WalkForwardSegment.get_pymongo_collection()
            operations = [
                InsertOne(s.model_dump(exclude={"id"})) for s in segments
            ]
            result = await collection.bulk_write(operations, ordered=False)
            logger.debug(
                "bulk_insert: %d segments inserted for run_id=%s",
                result.inserted_count,
                segments[0].run_id,
            )
            return result.inserted_count
        except Exception as exc:
            logger.error("bulk_insert failed: %s", exc, exc_info=True)
            raise DatabaseException("Bulk insert of WalkForwardSegment records failed.", detail=str(exc))
