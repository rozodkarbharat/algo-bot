"""
PaperPosition repository — data-access layer for the paper_positions collection.

Encapsulates Motor / Beanie I/O so services remain DB-agnostic. Uses raw
MongoDB filter dicts (Beanie 2.x requirement).

Duplicate-prevention strategy:
  - The (symbol, trading_date) uniqueness rule for OPEN positions is enforced
    at the service layer: it queries `get_open_for_symbol_and_date()` before
    inserting. This avoids the unique-index drag on closed-position history.
"""

from datetime import datetime
from typing import Optional

from pymongo import ReplaceOne

from app.core.exceptions import DatabaseException
from app.models.paper_position import PaperPosition, PaperPositionStatus
from app.repositories.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class PaperPositionRepository(BaseRepository[PaperPosition]):
    document_model = PaperPosition

    # ── Writes ────────────────────────────────────────────────────────────────

    async def insert(self, position: PaperPosition) -> PaperPosition:
        """Insert a new paper position."""
        try:
            return await position.insert()
        except Exception as exc:
            logger.error("Insert PaperPosition failed for %s: %s", position.symbol, exc)
            raise DatabaseException("Failed to insert PaperPosition.", detail=str(exc))

    async def upsert_by_position_id(self, position: PaperPosition) -> PaperPosition:
        """
        Replace the document keyed by position_id, or insert if absent.

        Used by the position manager to persist incremental mark-to-market
        updates without round-tripping a full read.
        """
        try:
            collection = PaperPosition.get_motor_collection()
            doc = position.model_dump(exclude={"id"})
            await collection.update_one(
                {"position_id": position.position_id},
                {"$set": doc},
                upsert=True,
            )
            return position
        except Exception as exc:
            logger.error(
                "Upsert PaperPosition failed for %s: %s", position.position_id, exc
            )
            raise DatabaseException(
                f"Failed to upsert PaperPosition {position.position_id}.",
                detail=str(exc),
            )

    async def bulk_upsert(self, positions: list[PaperPosition]) -> int:
        """Bulk persist many positions in a single round-trip."""
        if not positions:
            return 0
        try:
            collection = PaperPosition.get_motor_collection()
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
            logger.error("Bulk upsert PaperPosition failed: %s", exc, exc_info=True)
            raise DatabaseException(
                "Bulk upsert of PaperPosition failed.", detail=str(exc)
            )

    # ── Reads ─────────────────────────────────────────────────────────────────

    async def get_by_position_id(self, position_id: str) -> Optional[PaperPosition]:
        try:
            return await PaperPosition.find_one({"position_id": position_id})
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch PaperPosition {position_id}.", detail=str(exc)
            )

    async def get_open_positions(self) -> list[PaperPosition]:
        """Return every position currently in OPEN status, sorted by entry time."""
        try:
            return (
                await PaperPosition.find({"status": PaperPositionStatus.OPEN.value})
                .sort("opened_at")
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException("Failed to fetch open paper positions.", detail=str(exc))

    async def get_open_for_symbol_and_date(
        self, symbol: str, trading_date: datetime
    ) -> Optional[PaperPosition]:
        """Return the OPEN paper position for (symbol, trading_date), if any."""
        try:
            return await PaperPosition.find_one(
                {
                    "symbol": symbol.upper(),
                    "trading_date": trading_date,
                    "status": PaperPositionStatus.OPEN.value,
                }
            )
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch open position for {symbol}.", detail=str(exc)
            )

    async def get_for_date(self, trading_date: datetime) -> list[PaperPosition]:
        try:
            return (
                await PaperPosition.find({"trading_date": trading_date})
                .sort("opened_at")
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch positions for {trading_date.date()}.", detail=str(exc)
            )

    async def count_open(self) -> int:
        try:
            return await PaperPosition.find(
                {"status": PaperPositionStatus.OPEN.value}
            ).count()
        except Exception as exc:
            raise DatabaseException("Failed to count open positions.", detail=str(exc))

    async def list_recent(self, limit: int = 50, skip: int = 0) -> list[PaperPosition]:
        try:
            return (
                await PaperPosition.find({})
                .sort("-opened_at")
                .skip(skip)
                .limit(limit)
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException("Failed to list recent paper positions.", detail=str(exc))

    async def delete_for_date(self, trading_date: datetime) -> int:
        """Delete every paper position for `trading_date` (used by reset job)."""
        try:
            collection = PaperPosition.get_motor_collection()
            result = await collection.delete_many({"trading_date": trading_date})
            return result.deleted_count
        except Exception as exc:
            raise DatabaseException(
                f"Failed to delete paper positions for {trading_date.date()}.",
                detail=str(exc),
            )
