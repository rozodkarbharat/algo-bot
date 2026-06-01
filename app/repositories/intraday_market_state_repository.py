"""
IntradayMarketState repository — data-access layer for live engine state.

Stores the per-(symbol, trading_date) status row used by the live signal
engine to remember ORB boundaries, breakout detection and trade-locked flag
across ticks/candles within a single trading session.

All filter queries use raw MongoDB dicts (Beanie 2.x requirement).
"""

from datetime import datetime
from typing import Optional

from pymongo import ReplaceOne

from app.core.exceptions import DatabaseException
from app.models.intraday_market_state import IntradayMarketState
from app.repositories.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class IntradayMarketStateRepository(BaseRepository[IntradayMarketState]):
    document_model = IntradayMarketState

    # ── Writes ────────────────────────────────────────────────────────────────

    async def upsert(self, state: IntradayMarketState) -> IntradayMarketState:
        """
        Insert or replace a state row identified by (symbol, trading_date).
        """
        try:
            collection = IntradayMarketState.get_motor_collection()
            doc = state.model_dump(exclude={"id"})
            result = await collection.update_one(
                {"symbol": state.symbol, "trading_date": state.trading_date},
                {"$set": doc},
                upsert=True,
            )
            if result.upserted_id:
                state.id = result.upserted_id  # type: ignore[assignment]
            return state
        except Exception as exc:
            logger.error(
                "Upsert IntradayMarketState failed for %s %s: %s",
                state.symbol, state.trading_date.date(), exc,
            )
            raise DatabaseException(
                f"Failed to upsert IntradayMarketState for {state.symbol}.",
                detail=str(exc),
            )

    async def bulk_upsert(self, states: list[IntradayMarketState]) -> int:
        """Upsert many states in a single round-trip. Returns total written."""
        if not states:
            return 0
        try:
            collection = IntradayMarketState.get_motor_collection()
            operations = [
                ReplaceOne(
                    {"symbol": s.symbol, "trading_date": s.trading_date},
                    s.model_dump(exclude={"id"}),
                    upsert=True,
                )
                for s in states
            ]
            result = await collection.bulk_write(operations, ordered=False)
            return result.upserted_count + result.modified_count
        except Exception as exc:
            logger.error("Bulk upsert IntradayMarketState failed: %s", exc, exc_info=True)
            raise DatabaseException(
                "Bulk upsert of IntradayMarketState failed.", detail=str(exc)
            )

    async def delete_for_date(self, trading_date: datetime) -> int:
        """
        Delete all state rows for a trading date.

        Used by the session reset job to clear yesterday's rows before the
        next session boots. Returns the number of deleted documents.
        """
        try:
            collection = IntradayMarketState.get_motor_collection()
            result = await collection.delete_many({"trading_date": trading_date})
            logger.info(
                "Cleared %d IntradayMarketState rows for %s.",
                result.deleted_count, trading_date.date(),
            )
            return result.deleted_count
        except Exception as exc:
            raise DatabaseException(
                f"Failed to clear IntradayMarketState for {trading_date.date()}.",
                detail=str(exc),
            )

    # ── Reads ─────────────────────────────────────────────────────────────────

    async def get(
        self, symbol: str, trading_date: datetime
    ) -> Optional[IntradayMarketState]:
        try:
            return await IntradayMarketState.find_one(
                {"symbol": symbol.upper(), "trading_date": trading_date}
            )
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch IntradayMarketState for {symbol} on {trading_date.date()}.",
                detail=str(exc),
            )

    async def get_for_date(self, trading_date: datetime) -> list[IntradayMarketState]:
        """Return all state rows for a trading date."""
        try:
            return (
                await IntradayMarketState.find({"trading_date": trading_date})
                .sort("symbol")
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch IntradayMarketState for {trading_date.date()}.",
                detail=str(exc),
            )

    async def get_locked_for_date(
        self, trading_date: datetime
    ) -> list[IntradayMarketState]:
        """Return state rows where trade_locked=True (already signalled)."""
        try:
            return await IntradayMarketState.find(
                {"trading_date": trading_date, "trade_locked": True}
            ).to_list()
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch locked IntradayMarketState for {trading_date.date()}.",
                detail=str(exc),
            )
