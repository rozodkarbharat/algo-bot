"""
OneSideDay repository — data-access layer for the one_side_days collection.

All Beanie / MongoDB calls for one-side day records live here.
Uses raw MongoDB filter dicts (Beanie 2.x / Pydantic v2 requirement).
"""

from datetime import datetime, timezone
from typing import Optional

from pymongo import ReplaceOne

from app.core.exceptions import DatabaseException
from app.models.one_side_day import OneSideDay
from app.repositories.base_repository import BaseRepository
from app.utils.logger import get_logger
from app.utils.market_time import date_to_utc_midnight

logger = get_logger(__name__)


class OneSideDayRepository(BaseRepository[OneSideDay]):
    document_model = OneSideDay

    # ── Writes ────────────────────────────────────────────────────────────────

    async def upsert_record(self, record: OneSideDay) -> OneSideDay:
        """
        Insert or update a one-side day record identified by (symbol, trading_date).

        Uses Motor upsert to avoid read-before-write. Returns the saved document.
        """
        try:
            collection = OneSideDay.get_pymongo_collection()
            doc = record.model_dump(exclude={"id"})
            # Ensure datetime fields are serialized to native datetime objects.
            result = await collection.update_one(
                {
                    "symbol": record.symbol,
                    "trading_date": record.trading_date,
                },
                {"$set": doc},
                upsert=True,
            )
            if result.upserted_id:
                record.id = result.upserted_id  # type: ignore[assignment]
            return record
        except Exception as exc:
            logger.error(
                "upsert_record failed for %s %s: %s",
                record.symbol, record.trading_date.date(), exc,
            )
            raise DatabaseException(
                f"Failed to upsert OneSideDay for {record.symbol}.",
                detail=str(exc),
            )

    async def bulk_upsert(self, records: list[OneSideDay]) -> int:
        """
        Upsert many OneSideDay documents in a single bulk_write call.

        Returns the total number of records written (inserted + modified).
        """
        if not records:
            return 0
        try:
            collection = OneSideDay.get_pymongo_collection()
            operations = [
                ReplaceOne(
                    {"symbol": r.symbol, "trading_date": r.trading_date},
                    r.model_dump(exclude={"id"}),
                    upsert=True,
                )
                for r in records
            ]
            result = await collection.bulk_write(operations, ordered=False)
            written = result.upserted_count + result.modified_count
            logger.info("Bulk upsert: %d records written for %d ops.", written, len(records))
            return written
        except Exception as exc:
            logger.error("bulk_upsert failed: %s", exc, exc_info=True)
            raise DatabaseException("Bulk upsert of OneSideDay records failed.", detail=str(exc))

    # ── Reads ─────────────────────────────────────────────────────────────────

    async def get_by_symbol(
        self,
        symbol: str,
        limit: int = 500,
        skip: int = 0,
    ) -> list[OneSideDay]:
        """Return all one-side day records for a symbol, newest first."""
        try:
            return (
                await OneSideDay.find({"symbol": symbol.upper()})
                .sort("-trading_date")
                .skip(skip)
                .limit(limit)
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch OneSideDays for {symbol}.", detail=str(exc)
            )

    async def get_by_date(self, trading_date: datetime) -> list[OneSideDay]:
        """Return all one-side day records for a specific trading date."""
        try:
            return await OneSideDay.find({"trading_date": trading_date}).to_list()
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch OneSideDays for date {trading_date.date()}.",
                detail=str(exc),
            )

    async def get_between_dates(
        self,
        symbol: str,
        from_date: datetime,
        to_date: datetime,
    ) -> list[OneSideDay]:
        """Return all records for a symbol within [from_date, to_date], oldest first."""
        try:
            return (
                await OneSideDay.find(
                    {
                        "symbol": symbol.upper(),
                        "trading_date": {"$gte": from_date, "$lte": to_date},
                    }
                )
                .sort("trading_date")
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch OneSideDays for {symbol} in range.", detail=str(exc)
            )

    async def get_latest(self, symbol: str) -> Optional[OneSideDay]:
        """Return the most recent one-side day record for a symbol."""
        try:
            results = (
                await OneSideDay.find({"symbol": symbol.upper()})
                .sort("-trading_date")
                .limit(1)
                .to_list()
            )
            return results[0] if results else None
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch latest OneSideDay for {symbol}.", detail=str(exc)
            )

    async def get_one_side_only(
        self,
        symbol: str,
        from_date: datetime,
        to_date: datetime,
    ) -> list[OneSideDay]:
        """Return only the valid one-side days (is_one_side=True) for a symbol in range."""
        try:
            return (
                await OneSideDay.find(
                    {
                        "symbol": symbol.upper(),
                        "is_one_side": True,
                        "trading_date": {"$gte": from_date, "$lte": to_date},
                    }
                )
                .sort("trading_date")
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch one-side days for {symbol}.", detail=str(exc)
            )

    async def get_by_symbol_and_date(
        self, symbol: str, trading_date: datetime
    ) -> Optional[OneSideDay]:
        """Return a single record for (symbol, date), or None."""
        try:
            return await OneSideDay.find_one(
                {"symbol": symbol.upper(), "trading_date": trading_date}
            )
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch OneSideDay for {symbol} on {trading_date.date()}.",
                detail=str(exc),
            )

    async def count_for_symbol(self, symbol: str) -> int:
        """Return total records stored for a symbol."""
        try:
            return await OneSideDay.find({"symbol": symbol.upper()}).count()
        except Exception as exc:
            raise DatabaseException(
                f"Failed to count OneSideDays for {symbol}.", detail=str(exc)
            )
