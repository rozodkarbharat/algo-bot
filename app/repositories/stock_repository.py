"""
Stock repository — data-access layer for the Stock collection.

All Beanie / MongoDB calls for stocks live here.
Services import this class and call its methods; they never
touch Beanie or Motor directly.
"""

from typing import Optional

from pymongo.errors import BulkWriteError

from app.core.exceptions import DatabaseException
from app.models.stock import Stock
from app.repositories.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class StockRepository(BaseRepository[Stock]):
    document_model = Stock

    # ── Reads ─────────────────────────────────────────────────────────────────

    async def get_stock_by_symbol(self, symbol: str) -> Optional[Stock]:
        """Return a Stock by its ticker symbol, or None."""
        try:
            return await Stock.find_one({"symbol": symbol.upper()})
        except Exception as exc:
            raise DatabaseException(f"Failed to fetch stock {symbol}.", detail=str(exc))

    async def get_all_active_stocks(self) -> list[Stock]:
        """Return all stocks where is_active=True, ordered by symbol."""
        try:
            return await Stock.find({"is_active": True}).sort("symbol").to_list()
        except Exception as exc:
            raise DatabaseException("Failed to list active stocks.", detail=str(exc))

    async def get_stocks_by_index(self, index: str) -> list[Stock]:
        """Return all active stocks that belong to a given index (e.g. 'NIFTY50')."""
        try:
            return (
                await Stock.find({"is_active": True, "indices": index})
                .sort("symbol")
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException(f"Failed to list stocks for index {index}.", detail=str(exc))

    async def get_active_count(self) -> int:
        """Count active stocks."""
        try:
            return await Stock.find({"is_active": True}).count()
        except Exception as exc:
            raise DatabaseException("Failed to count active stocks.", detail=str(exc))

    # ── Writes ────────────────────────────────────────────────────────────────

    async def create_stock(self, stock: Stock) -> Stock:
        """Insert a new stock document."""
        return await self.create(stock)

    async def bulk_insert_stocks(
        self, stocks: list[Stock], skip_duplicates: bool = True
    ) -> int:
        """
        Insert multiple stocks at once.

        When skip_duplicates=True, duplicate key errors (symbol/token already
        exists) are silently skipped and the count of newly inserted documents
        is returned. Set skip_duplicates=False to raise on any duplicate.
        """
        if not stocks:
            return 0
        try:
            result = await Stock.insert_many(stocks, ordered=not skip_duplicates)
            inserted = len(result.inserted_ids) if result else 0
            logger.info("Bulk inserted %d stocks.", inserted)
            return inserted
        except BulkWriteError as exc:
            if not skip_duplicates:
                raise DatabaseException("Bulk insert failed — duplicate stocks.", detail=str(exc))
            # Count only the successfully inserted docs
            inserted = exc.details.get("nInserted", 0)
            logger.info(
                "Bulk insert: %d inserted, %d skipped (duplicates).",
                inserted,
                len(stocks) - inserted,
            )
            return inserted
        except Exception as exc:
            raise DatabaseException("Bulk stock insert failed.", detail=str(exc))

    async def deactivate_stock(self, symbol: str) -> bool:
        """Set is_active=False for a stock. Returns True if updated."""
        stock = await self.get_stock_by_symbol(symbol.upper())
        if stock is None:
            return False
        stock.is_active = False
        stock.mark_updated()
        await stock.save()
        return True

    async def upsert_stock(self, stock: Stock) -> Stock:
        """
        Insert or update a stock by symbol.
        If a document with the same symbol exists, update its fields.
        """
        existing = await self.get_stock_by_symbol(stock.symbol)
        if existing is None:
            return await self.create(stock)
        existing.instrument_token = stock.instrument_token
        existing.company_name = stock.company_name
        existing.indices = stock.indices
        existing.sector = stock.sector
        existing.is_active = stock.is_active
        existing.mark_updated()
        return await self.save(existing)
