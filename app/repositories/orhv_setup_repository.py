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

    async def get_all_on_date(self, setup_date: datetime) -> list[ORHVSetup]:
        """Return every Phase 1 result (candidates and rejected) for a date."""
        return await ORHVSetup.find(ORHVSetup.setup_date == setup_date).to_list()

    async def get_distinct_setup_dates(
        self,
        from_date: datetime,
        to_date: datetime,
        symbols: Optional[list[str]] = None,
    ):
        """
        Return the set of distinct setup dates (as date objects) that already
        have Phase 1 detection stored within [from_date, to_date].

        When ``symbols`` is given, only those symbols are considered. Used by the
        ORHV history guard to skip days that were already detected.
        """
        from datetime import date as _date

        collection = ORHVSetup.get_pymongo_collection()
        query: dict = {"setup_date": {"$gte": from_date, "$lte": to_date}}
        if symbols:
            query["symbol"] = {"$in": [s.upper() for s in symbols]}
        raw = await collection.distinct("setup_date", query)
        result: set[_date] = set()
        for dt in raw:
            if isinstance(dt, datetime):
                result.add(dt.date())
            elif isinstance(dt, _date):
                result.add(dt)
        return result

    async def get_setup_symbol_counts_by_date(
        self,
        from_date: datetime,
        to_date: datetime,
        symbols: Optional[list[str]] = None,
    ) -> "dict":
        """
        Return ``{date: distinct_symbol_count}`` for stored setups within
        [from_date, to_date].

        Unlike ``get_distinct_setup_dates`` (which only tells you a day has *some*
        setup), this exposes how many distinct symbols were detected on each day.
        The history guard uses it to tell a fully-detected universe day apart from
        one that only has a handful of stragglers (e.g. earlier single-symbol
        tests), so partial history doesn't silently block a full backfill.
        """
        from datetime import date as _date

        collection = ORHVSetup.get_pymongo_collection()
        match: dict = {"setup_date": {"$gte": from_date, "$lte": to_date}}
        if symbols:
            match["symbol"] = {"$in": [s.upper() for s in symbols]}
        pipeline = [
            {"$match": match},
            {"$group": {"_id": "$setup_date", "syms": {"$addToSet": "$symbol"}}},
            {"$project": {"n": {"$size": "$syms"}}},
        ]
        counts: dict[_date, int] = {}
        async for row in collection.aggregate(pipeline):
            dt = row["_id"]
            key = dt.date() if isinstance(dt, datetime) else dt
            counts[key] = int(row.get("n", 0))
        return counts

    async def bulk_upsert(self, docs: list[ORHVSetup]) -> int:
        """Upsert a batch; returns count of processed docs."""
        count = 0
        for doc in docs:
            await self.upsert(doc)
            count += 1
        return count
