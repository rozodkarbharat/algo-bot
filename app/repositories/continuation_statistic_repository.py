"""
ContinuationStatistic repository — data-access layer for the continuation_statistics collection.

One document per symbol. All upserts are keyed on symbol (unique index).
Uses raw MongoDB filter dicts (Beanie 2.x / Pydantic v2 requirement).
"""

from typing import Optional

from app.core.exceptions import DatabaseException
from app.models.continuation_statistic import ContinuationStatistic
from app.repositories.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ContinuationStatisticRepository(BaseRepository[ContinuationStatistic]):
    document_model = ContinuationStatistic

    # ── Writes ────────────────────────────────────────────────────────────────

    async def upsert_statistic(self, stat: ContinuationStatistic) -> ContinuationStatistic:
        """
        Insert or update the continuation statistic for a symbol.

        Keyed on symbol (unique index). Uses Motor upsert to avoid read-before-write.
        """
        try:
            collection = ContinuationStatistic.get_pymongo_collection()
            doc = stat.model_dump(exclude={"id"})
            result = await collection.update_one(
                {"symbol": stat.symbol},
                {"$set": doc},
                upsert=True,
            )
            if result.upserted_id:
                stat.id = result.upserted_id  # type: ignore[assignment]
            return stat
        except Exception as exc:
            logger.error("upsert_statistic failed for %s: %s", stat.symbol, exc)
            raise DatabaseException(
                f"Failed to upsert ContinuationStatistic for {stat.symbol}.",
                detail=str(exc),
            )

    # ── Reads ─────────────────────────────────────────────────────────────────

    async def get_by_symbol(self, symbol: str) -> Optional[ContinuationStatistic]:
        """Return the continuation statistic for a symbol, or None."""
        try:
            return await ContinuationStatistic.find_one({"symbol": symbol.upper()})
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch ContinuationStatistic for {symbol}.", detail=str(exc)
            )

    async def get_tradable_stocks(self) -> list[ContinuationStatistic]:
        """Return all symbols where tradable=True, ordered by probability descending."""
        try:
            return (
                await ContinuationStatistic.find({"tradable": True})
                .sort("-continuation_probability")
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException("Failed to fetch tradable stocks.", detail=str(exc))

    async def get_top_probability_stocks(
        self, limit: int = 20
    ) -> list[ContinuationStatistic]:
        """
        Return stocks with the highest continuation probability, regardless of tradable flag.

        Useful for analytics and tuning the threshold.
        """
        try:
            return (
                await ContinuationStatistic.find({"total_occurrences": {"$gt": 0}})
                .sort("-continuation_probability")
                .limit(limit)
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException(
                "Failed to fetch top probability stocks.", detail=str(exc)
            )

    async def get_all_statistics(
        self, limit: int = 100, skip: int = 0
    ) -> list[ContinuationStatistic]:
        """Return all continuation statistics ordered by probability descending."""
        try:
            return (
                await ContinuationStatistic.find({})
                .sort("-continuation_probability")
                .skip(skip)
                .limit(limit)
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException(
                "Failed to list continuation statistics.", detail=str(exc)
            )

    async def count_tradable(self) -> int:
        """Count symbols where tradable=True."""
        try:
            return await ContinuationStatistic.find({"tradable": True}).count()
        except Exception as exc:
            raise DatabaseException("Failed to count tradable symbols.", detail=str(exc))
