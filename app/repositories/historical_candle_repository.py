"""
HistoricalCandle repository — data-access layer for the historical_candles collection.

Key design:
  - save_daily_candles() performs an upsert via Motor directly to avoid
    the round-trip of a Beanie find-then-save pattern.
  - get_candles_between_dates() leverages the compound index
    (symbol, interval, trading_date) for efficient range scans.
"""

from datetime import datetime, timezone
from typing import Optional

from pymongo import ASCENDING, ReplaceOne

from app.core.exceptions import DatabaseException
from app.models.historical_candle import CandleData, HistoricalCandle
from app.repositories.base_repository import BaseRepository
from app.utils.logger import get_logger
from app.utils.market_time import date_to_utc_midnight

logger = get_logger(__name__)


class HistoricalCandleRepository(BaseRepository[HistoricalCandle]):
    document_model = HistoricalCandle

    # ── Writes ────────────────────────────────────────────────────────────────

    async def save_daily_candles(
        self,
        symbol: str,
        exchange: str,
        interval: str,
        trading_date: datetime,
        candles: list[CandleData],
    ) -> bool:
        """
        Upsert a single day-bucket of candles.

        Uses Motor's update_one with upsert=True on the compound key
        (symbol, trading_date, interval). This is atomic and avoids
        the read-before-write cost of a Beanie save().

        Returns True if a new document was inserted, False if updated.
        """
        try:
            collection = HistoricalCandle.get_motor_collection()
            candle_dicts = [c.model_dump() for c in candles]
            result = await collection.update_one(
                {
                    "symbol": symbol,
                    "trading_date": trading_date,
                    "interval": interval,
                },
                {
                    "$set": {
                        "exchange": exchange,
                        "candles": candle_dicts,
                        "candle_count": len(candles),
                        "fetched_at": datetime.now(timezone.utc),
                    },
                    "$setOnInsert": {
                        "symbol": symbol,
                        "trading_date": trading_date,
                        "interval": interval,
                    },
                },
                upsert=True,
            )
            return result.upserted_id is not None  # True = new insert
        except Exception as exc:
            logger.error("save_daily_candles failed for %s %s: %s", symbol, trading_date.date(), exc)
            raise DatabaseException(
                f"Failed to upsert candles for {symbol} on {trading_date.date()}.",
                detail=str(exc),
            )

    async def bulk_insert(self, buckets: list[HistoricalCandle]) -> int:
        """
        Upsert multiple day-buckets efficiently using bulk_write.

        Uses ReplaceOne with upsert=True per bucket. Returns total
        number of documents inserted (not updated).
        """
        if not buckets:
            return 0
        try:
            collection = HistoricalCandle.get_motor_collection()
            operations = [
                ReplaceOne(
                    {
                        "symbol": b.symbol,
                        "trading_date": b.trading_date,
                        "interval": b.interval,
                    },
                    b.model_dump(exclude={"id"}),
                    upsert=True,
                )
                for b in buckets
            ]
            result = await collection.bulk_write(operations, ordered=False)
            inserted = result.upserted_count
            logger.debug("bulk_insert: %d upserted, %d modified.", inserted, result.modified_count)
            return inserted
        except Exception as exc:
            raise DatabaseException("Bulk candle insert failed.", detail=str(exc))

    # ── Reads ─────────────────────────────────────────────────────────────────

    async def get_candles_by_symbol(
        self,
        symbol: str,
        interval: str,
        limit: int = 30,
    ) -> list[HistoricalCandle]:
        """Return the most recent `limit` day-buckets for a symbol."""
        try:
            return (
                await HistoricalCandle.find({"symbol": symbol, "interval": interval})
                .sort("-trading_date")
                .limit(limit)
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException(f"Failed to fetch candles for {symbol}.", detail=str(exc))

    async def get_candles_between_dates(
        self,
        symbol: str,
        interval: str,
        from_date: datetime,
        to_date: datetime,
    ) -> list[HistoricalCandle]:
        """
        Return all day-buckets for a symbol within [from_date, to_date].

        Results are sorted by trading_date ascending (oldest first) — the
        natural order for backtesting / strategy processing.
        """
        try:
            return (
                await HistoricalCandle.find({
                    "symbol": symbol,
                    "interval": interval,
                    "trading_date": {"$gte": from_date, "$lte": to_date},
                })
                .sort("trading_date")
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch candles for {symbol} between dates.", detail=str(exc)
            )

    async def get_latest_candle_date(
        self, symbol: str, interval: str
    ) -> Optional[datetime]:
        """
        Return the most recent trading_date stored for a symbol.

        Used by the ingestion service to determine the resume point:
            sync from (latest_date + 1 day) to today.
        Returns None if no data exists for this symbol.
        """
        try:
            bucket = await (
                HistoricalCandle.find({"symbol": symbol, "interval": interval})
                .sort("-trading_date")
                .limit(1)
                .first_or_none()
            )
            return bucket.trading_date if bucket else None
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch latest candle date for {symbol}.", detail=str(exc)
            )

    async def get_existing_dates(
        self,
        symbol: str,
        interval: str,
        from_date: datetime,
        to_date: datetime,
    ) -> set[datetime]:
        """
        Return the set of trading_dates already stored for a symbol in a range.

        Used by the ingestion service to skip already-present dates.
        """
        try:
            collection = HistoricalCandle.get_motor_collection()
            cursor = collection.find(
                {
                    "symbol": symbol,
                    "interval": interval,
                    "trading_date": {"$gte": from_date, "$lte": to_date},
                },
                {"trading_date": 1, "_id": 0},
            )
            docs = await cursor.to_list(length=None)
            return {doc["trading_date"] for doc in docs}
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch existing dates for {symbol}.", detail=str(exc)
            )

    async def count_by_symbol(self, symbol: str, interval: str) -> int:
        """Return total day-buckets stored for a symbol."""
        try:
            return await HistoricalCandle.find(
                {"symbol": symbol, "interval": interval}
            ).count()
        except Exception as exc:
            raise DatabaseException(f"Failed to count candles for {symbol}.", detail=str(exc))
