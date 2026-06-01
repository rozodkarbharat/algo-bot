"""
Repository for StockPerformanceAnalytics documents.

Each symbol has at most one document (unique index). Updates use Motor
upsert so a research run can refresh analytics without creating duplicates.
"""

from datetime import datetime, timezone
from typing import Optional

from pymongo import UpdateOne

from app.core.exceptions import DatabaseException
from app.models.stock_performance_analytics import StockPerformanceAnalytics
from app.repositories.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class StockPerformanceAnalyticsRepository(BaseRepository[StockPerformanceAnalytics]):
    document_model = StockPerformanceAnalytics

    # ── Reads ─────────────────────────────────────────────────────────────────

    async def get_by_symbol(self, symbol: str) -> Optional[StockPerformanceAnalytics]:
        """Return analytics for a single symbol, or None if not yet computed."""
        return await StockPerformanceAnalytics.find_one({"symbol": symbol})

    async def get_top_performers(
        self,
        metric: str = "total_pnl",
        limit: int = 10,
        min_trades: int = 5,
    ) -> list[StockPerformanceAnalytics]:
        """Return top-N stocks sorted by the given metric (descending)."""
        sort_key = f"-{metric}"
        return (
            await StockPerformanceAnalytics.find({"total_trades": {"$gte": min_trades}})
            .sort(sort_key)
            .limit(limit)
            .to_list()
        )

    async def get_worst_performers(
        self,
        metric: str = "total_pnl",
        limit: int = 10,
        min_trades: int = 5,
    ) -> list[StockPerformanceAnalytics]:
        """Return bottom-N stocks sorted by the given metric (ascending)."""
        return (
            await StockPerformanceAnalytics.find({"total_trades": {"$gte": min_trades}})
            .sort(metric)
            .limit(limit)
            .to_list()
        )

    async def get_all_ranked(
        self,
        metric: str = "expectancy",
        limit: int = 100,
        min_trades: int = 3,
    ) -> list[StockPerformanceAnalytics]:
        """Return all symbols ranked by metric (descending), filtered by min_trades."""
        return (
            await StockPerformanceAnalytics.find({"total_trades": {"$gte": min_trades}})
            .sort(f"-{metric}")
            .limit(limit)
            .to_list()
        )

    # ── Writes ────────────────────────────────────────────────────────────────

    async def upsert_bulk(
        self, records: list[StockPerformanceAnalytics]
    ) -> int:
        """
        Upsert many StockPerformanceAnalytics documents in a single Motor call.

        Matches on symbol (unique); replaces the full document on conflict.
        Returns the number of documents written (inserted + modified).
        """
        if not records:
            return 0
        try:
            collection = StockPerformanceAnalytics.get_motor_collection()
            ops = [
                UpdateOne(
                    {"symbol": rec.symbol},
                    {"$set": rec.model_dump(exclude={"id"})},
                    upsert=True,
                )
                for rec in records
            ]
            result = await collection.bulk_write(ops, ordered=False)
            count = result.upserted_count + result.modified_count
            logger.debug(
                "StockPerformanceAnalyticsRepository.upsert_bulk: %d upserted, %d modified.",
                result.upserted_count,
                result.modified_count,
            )
            return count
        except Exception as exc:
            logger.error("upsert_bulk failed: %s", exc)
            raise DatabaseException(
                "Failed to upsert stock performance analytics.", detail=str(exc)
            )
