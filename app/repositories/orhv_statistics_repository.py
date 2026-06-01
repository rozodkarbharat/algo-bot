"""
Repository for ORHVStatistics documents (per-symbol rolling statistics).
"""

from typing import Optional

from app.repositories.base_repository import BaseRepository
from app.strategy.strategies.opening_range_historical_validation.models import ORHVStatistics
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ORHVStatisticsRepository(BaseRepository[ORHVStatistics]):
    document_model = ORHVStatistics

    async def get_by_symbol(self, symbol: str) -> Optional[ORHVStatistics]:
        return await ORHVStatistics.find_one(
            ORHVStatistics.symbol == symbol.upper()
        )

    async def upsert(self, doc: ORHVStatistics) -> ORHVStatistics:
        existing = await self.get_by_symbol(doc.symbol)
        if existing:
            doc.id = existing.id
            await doc.replace()
            return doc
        return await doc.insert()

    async def get_all_sorted_by_win_rate(
        self, min_setups: int = 1, limit: int = 100
    ) -> list[ORHVStatistics]:
        """Return symbols ranked by current_win_rate, filtered by minimum setup count."""
        return await ORHVStatistics.find(
            ORHVStatistics.total_setups_detected >= min_setups,
        ).sort(-ORHVStatistics.current_win_rate).limit(limit).to_list()

    async def get_tradable(self, limit: int = 100) -> list[ORHVStatistics]:
        """Return symbols with at least one tradable setup, ranked by win rate."""
        return await ORHVStatistics.find(
            ORHVStatistics.tradable_setups > 0,
        ).sort(-ORHVStatistics.current_win_rate).limit(limit).to_list()
