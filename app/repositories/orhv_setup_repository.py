"""
Repository for ORHVSetup documents (Phase 1 detection results).
"""

from datetime import datetime
from typing import Optional

from app.repositories.base_repository import BaseRepository
from app.strategy.strategies.opening_range_historical_validation.models import ORHVSetup
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ORHVSetupRepository(BaseRepository[ORHVSetup]):
    document_model = ORHVSetup

    async def get_by_symbol_and_date(
        self, symbol: str, setup_date: datetime
    ) -> Optional[ORHVSetup]:
        return await ORHVSetup.find_one(
            ORHVSetup.symbol == symbol.upper(),
            ORHVSetup.setup_date == setup_date,
        )

    async def upsert(self, doc: ORHVSetup) -> ORHVSetup:
        """Insert or replace by (symbol, setup_date)."""
        existing = await self.get_by_symbol_and_date(doc.symbol, doc.setup_date)
        if existing:
            doc.id = existing.id
            await doc.replace()
            return doc
        return await doc.insert()

    async def get_candidates_before_date(
        self,
        symbol: str,
        before_date: datetime,
        limit: int = 200,
    ) -> list[ORHVSetup]:
        """Return candidate setups for symbol before a given date, most-recent first."""
        return await ORHVSetup.find(
            ORHVSetup.symbol == symbol.upper(),
            ORHVSetup.is_candidate == True,
            ORHVSetup.setup_date < before_date,
        ).sort(-ORHVSetup.setup_date).limit(limit).to_list()

    async def get_between_dates(
        self,
        symbol: str,
        from_date: datetime,
        to_date: datetime,
    ) -> list[ORHVSetup]:
        return await ORHVSetup.find(
            ORHVSetup.symbol == symbol.upper(),
            ORHVSetup.setup_date >= from_date,
            ORHVSetup.setup_date <= to_date,
        ).sort(+ORHVSetup.setup_date).to_list()

    async def get_candidates_on_date(self, setup_date: datetime) -> list[ORHVSetup]:
        """Return all candidate setups across all symbols for a given date."""
        return await ORHVSetup.find(
            ORHVSetup.setup_date == setup_date,
            ORHVSetup.is_candidate == True,
        ).to_list()

    async def bulk_upsert(self, docs: list[ORHVSetup]) -> int:
        """Upsert a batch; returns count of processed docs."""
        count = 0
        for doc in docs:
            await self.upsert(doc)
            count += 1
        return count
