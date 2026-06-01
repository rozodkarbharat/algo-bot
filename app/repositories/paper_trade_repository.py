"""
PaperTrade repository — append-only ledger of completed paper trades.

Writes are insert-only (no updates) so the audit trail is immutable. Reads
support trade-history pagination, daily summaries, and equity-curve queries.
"""

from datetime import datetime
from typing import Optional

from app.core.exceptions import DatabaseException
from app.models.paper_trade import PaperTrade
from app.repositories.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class PaperTradeRepository(BaseRepository[PaperTrade]):
    document_model = PaperTrade

    # ── Writes ────────────────────────────────────────────────────────────────

    async def insert(self, trade: PaperTrade) -> PaperTrade:
        try:
            return await trade.insert()
        except Exception as exc:
            logger.error("Insert PaperTrade failed for %s: %s", trade.symbol, exc)
            raise DatabaseException("Failed to insert PaperTrade.", detail=str(exc))

    # ── Reads ─────────────────────────────────────────────────────────────────

    async def get_by_trade_id(self, trade_id: str) -> Optional[PaperTrade]:
        try:
            return await PaperTrade.find_one({"trade_id": trade_id})
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch PaperTrade {trade_id}.", detail=str(exc)
            )

    async def get_for_date(self, trading_date: datetime) -> list[PaperTrade]:
        try:
            return (
                await PaperTrade.find({"trading_date": trading_date})
                .sort("closed_at")
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch PaperTrades for {trading_date.date()}.", detail=str(exc)
            )

    async def get_for_symbol(
        self, symbol: str, limit: int = 100, skip: int = 0
    ) -> list[PaperTrade]:
        try:
            return (
                await PaperTrade.find({"symbol": symbol.upper()})
                .sort("-closed_at")
                .skip(skip)
                .limit(limit)
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch PaperTrades for {symbol}.", detail=str(exc)
            )

    async def list_recent(self, limit: int = 100, skip: int = 0) -> list[PaperTrade]:
        try:
            return (
                await PaperTrade.find({})
                .sort("-closed_at")
                .skip(skip)
                .limit(limit)
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException("Failed to list recent PaperTrades.", detail=str(exc))

    async def count_for_date(self, trading_date: datetime) -> int:
        try:
            return await PaperTrade.find({"trading_date": trading_date}).count()
        except Exception as exc:
            raise DatabaseException(
                f"Failed to count PaperTrades for {trading_date.date()}.", detail=str(exc)
            )

    async def list_between(
        self, from_dt: datetime, to_dt: datetime
    ) -> list[PaperTrade]:
        """Return all trades closed within [from_dt, to_dt] (UTC), oldest first."""
        try:
            return (
                await PaperTrade.find(
                    {"closed_at": {"$gte": from_dt, "$lte": to_dt}}
                )
                .sort("closed_at")
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException(
                "Failed to fetch PaperTrades in date range.", detail=str(exc)
            )
