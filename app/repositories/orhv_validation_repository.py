"""
Repository for ORHVValidationRecord documents (Phase 2 validation results).
"""

from datetime import datetime
from typing import Optional

from app.repositories.base_repository import BaseRepository
from app.strategy.strategies.opening_range_historical_validation.models import ORHVValidationRecord
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ORHVValidationRepository(BaseRepository[ORHVValidationRecord]):
    document_model = ORHVValidationRecord

    async def get_by_symbol_and_date(
        self, symbol: str, candidate_date: datetime
    ) -> Optional[ORHVValidationRecord]:
        return await ORHVValidationRecord.find_one(
            ORHVValidationRecord.symbol == symbol.upper(),
            ORHVValidationRecord.candidate_date == candidate_date,
        )

    async def upsert(self, doc: ORHVValidationRecord) -> ORHVValidationRecord:
        existing = await self.get_by_symbol_and_date(doc.symbol, doc.candidate_date)
        if existing:
            doc.id = existing.id
            await doc.replace()
            return doc
        return await doc.insert()

    async def get_tradable_for_date(
        self, execution_date: datetime
    ) -> list[ORHVValidationRecord]:
        """Return all tradable validations for a given execution date (Day D+1)."""
        return await ORHVValidationRecord.find(
            ORHVValidationRecord.execution_date == execution_date,
            ORHVValidationRecord.tradable == True,
        ).to_list()

    async def get_for_candidate_date(
        self, candidate_date: datetime
    ) -> list[ORHVValidationRecord]:
        """Return all Phase 2 validation rows for setups detected on candidate_date (Day D)."""
        return await ORHVValidationRecord.find(
            ORHVValidationRecord.candidate_date == candidate_date,
        ).to_list()

    async def get_recent_for_symbol(
        self, symbol: str, limit: int = 30
    ) -> list[ORHVValidationRecord]:
        return await ORHVValidationRecord.find(
            ORHVValidationRecord.symbol == symbol.upper(),
        ).sort(-ORHVValidationRecord.candidate_date).limit(limit).to_list()

    async def get_between_dates(
        self, symbol: str, from_date: datetime, to_date: datetime
    ) -> list[ORHVValidationRecord]:
        return await ORHVValidationRecord.find(
            ORHVValidationRecord.symbol == symbol.upper(),
            ORHVValidationRecord.candidate_date >= from_date,
            ORHVValidationRecord.candidate_date <= to_date,
        ).sort(+ORHVValidationRecord.candidate_date).to_list()
