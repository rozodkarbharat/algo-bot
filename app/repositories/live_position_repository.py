"""
LivePosition repository — data-access layer for the live_positions collection.

Pattern mirrors PaperPositionRepository. Uses raw MongoDB filter dicts
(Beanie 2.x requirement) and Motor for high-throughput upserts.
"""

from datetime import datetime
from typing import Optional

from pymongo import ReplaceOne

from app.core.exceptions import DatabaseException
from app.models.live_position import LivePosition, LivePositionStatus
from app.repositories.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class LivePositionRepository(BaseRepository[LivePosition]):
    document_model = LivePosition

    # ── Writes ────────────────────────────────────────────────────────────────

    async def insert(self, position: LivePosition) -> LivePosition:
        try:
            return await position.insert()
        except Exception as exc:
            logger.error("Insert LivePosition failed for %s: %s", position.symbol, exc)
            raise DatabaseException("Failed to insert LivePosition.", detail=str(exc))

    async def upsert_by_position_id(self, position: LivePosition) -> LivePosition:
        try:
            position.mark_updated()
            collection = LivePosition.get_pymongo_collection()
            doc = position.model_dump(exclude={"id"})
            await collection.update_one(
                {"position_id": position.position_id},
                {"$set": doc},
                upsert=True,
            )
            return position
        except Exception as exc:
            logger.error("Upsert LivePosition failed for %s: %s", position.position_id, exc)
            raise DatabaseException(
                f"Failed to upsert LivePosition {position.position_id}.",
                detail=str(exc),
            )

    async def bulk_upsert(self, positions: list[LivePosition]) -> int:
        if not positions:
            return 0
        try:
            collection = LivePosition.get_pymongo_collection()
            ops = [
                ReplaceOne(
                    {"position_id": p.position_id},
                    p.model_dump(exclude={"id"}),
                    upsert=True,
                )
                for p in positions
            ]
            result = await collection.bulk_write(ops, ordered=False)
            return result.upserted_count + result.modified_count
        except Exception as exc:
            logger.error("Bulk upsert LivePosition failed: %s", exc, exc_info=True)
            raise DatabaseException("Bulk upsert of LivePosition failed.", detail=str(exc))

    # ── Reads ─────────────────────────────────────────────────────────────────

    async def get_by_position_id(self, position_id: str) -> Optional[LivePosition]:
        try:
            return await LivePosition.find_one({"position_id": position_id})
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch LivePosition {position_id}.", detail=str(exc)
            )

    async def get_open_positions(
        self, broker_name: Optional[str] = None
    ) -> list[LivePosition]:
        try:
            query: dict = {"status": LivePositionStatus.OPEN.value}
            if broker_name is not None:
                query["broker_name"] = broker_name
            return (
                await LivePosition.find(query)
                .sort("opened_at")
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException(
                "Failed to fetch open live positions.", detail=str(exc)
            )

    async def get_open_for_symbol_and_date(
        self, symbol: str, trading_date: datetime
    ) -> Optional[LivePosition]:
        try:
            return await LivePosition.find_one(
                {
                    "symbol": symbol.upper(),
                    "trading_date": trading_date,
                    "status": LivePositionStatus.OPEN.value,
                }
            )
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch open live position for {symbol}.", detail=str(exc)
            )

    async def get_for_date(self, trading_date: datetime) -> list[LivePosition]:
        try:
            return (
                await LivePosition.find({"trading_date": trading_date})
                .sort("opened_at")
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch live positions for {trading_date.date()}.",
                detail=str(exc),
            )

    async def list_recent(self, limit: int = 50, skip: int = 0) -> list[LivePosition]:
        try:
            return (
                await LivePosition.find({})
                .sort("-opened_at")
                .skip(skip)
                .limit(limit)
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException(
                "Failed to list recent live positions.", detail=str(exc)
            )

    async def get_closed_between(
        self,
        from_dt: datetime,
        to_dt: datetime,
        broker_name: Optional[str] = None,
    ) -> list[LivePosition]:
        """Return all CLOSED live positions whose trading_date falls in [from_dt, to_dt]."""
        query: dict = {
            "status": LivePositionStatus.CLOSED.value,
            "trading_date": {"$gte": from_dt, "$lte": to_dt},
        }
        if broker_name is not None:
            query["broker_name"] = broker_name
        try:
            return (
                await LivePosition.find(query)
                .sort("trading_date")
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException(
                "Failed to fetch closed live positions for date range.",
                detail=str(exc),
            )

    async def count_open(self, broker_name: Optional[str] = None) -> int:
        query: dict = {"status": LivePositionStatus.OPEN.value}
        if broker_name is not None:
            query["broker_name"] = broker_name
        try:
            return await LivePosition.find(query).count()
        except Exception as exc:
            raise DatabaseException("Failed to count open live positions.", detail=str(exc))
