"""
Repository for ORHVSignalRecord documents (Phase 3 live signals).
"""

from datetime import datetime
from typing import Optional

from pymongo.errors import DuplicateKeyError

from app.repositories.base_repository import BaseRepository
from app.strategy.strategies.opening_range_historical_validation.models import (
    ORHVSignalRecord,
    ORHVSignalStatus,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)


class DuplicateORHVSignalError(Exception):
    pass


class ORHVSignalRepository(BaseRepository[ORHVSignalRecord]):
    document_model = ORHVSignalRecord

    async def insert_unique(self, doc: ORHVSignalRecord) -> ORHVSignalRecord:
        """Insert with unique (symbol, trading_date) enforcement."""
        try:
            return await doc.insert()
        except DuplicateKeyError:
            raise DuplicateORHVSignalError(
                f"Signal already exists for {doc.symbol} on {doc.trading_date.date()}"
            )

    async def get_by_signal_id(self, signal_id: str) -> Optional[ORHVSignalRecord]:
        return await ORHVSignalRecord.find_one(ORHVSignalRecord.signal_id == signal_id)

    async def get_by_symbol_and_date(
        self, symbol: str, trading_date: datetime
    ) -> Optional[ORHVSignalRecord]:
        return await ORHVSignalRecord.find_one(
            ORHVSignalRecord.symbol == symbol.upper(),
            ORHVSignalRecord.trading_date == trading_date,
        )

    async def get_for_date(self, trading_date: datetime) -> list[ORHVSignalRecord]:
        return await ORHVSignalRecord.find(
            ORHVSignalRecord.trading_date == trading_date,
        ).sort(-ORHVSignalRecord.created_at).to_list()

    async def get_recent(self, limit: int = 50) -> list[ORHVSignalRecord]:
        return await ORHVSignalRecord.find().sort(
            -ORHVSignalRecord.created_at
        ).limit(limit).to_list()

    async def count_for_date(self, trading_date: datetime) -> int:
        return await ORHVSignalRecord.find(
            ORHVSignalRecord.trading_date == trading_date,
        ).count()
