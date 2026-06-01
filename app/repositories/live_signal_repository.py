"""
LiveSignal repository — data-access layer for the live_signals collection.

Repositories wrap all MongoDB I/O so services remain DB-agnostic. Uses raw
MongoDB filter dicts (Beanie 2.x / Pydantic v2 requirement) and Motor for
upsert performance.

Duplicate-prevention strategy:
  - Insert is wrapped to translate the MongoDB DuplicateKeyError raised by the
    unique (symbol, trading_date) index into a typed application exception.
    This lets the live engine treat duplicate suppression as a normal,
    expected outcome rather than a generic database error.
"""

from datetime import datetime
from typing import Optional

from pymongo.errors import DuplicateKeyError

from app.core.exceptions import DatabaseException, ValidationException
from app.models.live_signal import LiveSignal, LiveSignalStatus
from app.repositories.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class DuplicateSignalError(ValidationException):
    """Raised when a (symbol, trading_date) signal already exists."""

    def __init__(self, symbol: str, trading_date: datetime) -> None:
        super().__init__(
            message=f"Live signal already exists for {symbol} on {trading_date.date()}.",
            detail={"symbol": symbol, "trading_date": trading_date.isoformat()},
        )


class LiveSignalRepository(BaseRepository[LiveSignal]):
    document_model = LiveSignal

    # ── Writes ────────────────────────────────────────────────────────────────

    async def insert_unique(self, signal: LiveSignal) -> LiveSignal:
        """
        Insert a signal, raising DuplicateSignalError on (symbol, date) collision.

        The unique index on (symbol, trading_date) guarantees that only one
        signal per stock per day can be persisted. The translated exception
        keeps duplicate handling clean in the service layer.
        """
        try:
            return await signal.insert()
        except DuplicateKeyError:
            logger.info(
                "Duplicate signal blocked: %s on %s",
                signal.symbol,
                signal.trading_date.date(),
            )
            raise DuplicateSignalError(signal.symbol, signal.trading_date)
        except Exception as exc:
            logger.error("insert_unique failed for %s: %s", signal.symbol, exc, exc_info=True)
            raise DatabaseException("Failed to insert LiveSignal.", detail=str(exc))

    async def update_status(
        self, signal_id: str, status: LiveSignalStatus
    ) -> Optional[LiveSignal]:
        """Update a signal's status by signal_id; returns the updated document."""
        try:
            collection = LiveSignal.get_motor_collection()
            result = await collection.find_one_and_update(
                {"signal_id": signal_id},
                {"$set": {"signal_status": status.value}},
                return_document=True,  # ReturnDocument.AFTER in newer pymongo
            )
            if result is None:
                return None
            return LiveSignal.model_validate(result)
        except Exception as exc:
            logger.error("update_status failed for %s: %s", signal_id, exc)
            raise DatabaseException("Failed to update LiveSignal status.", detail=str(exc))

    # ── Reads ─────────────────────────────────────────────────────────────────

    async def get_by_signal_id(self, signal_id: str) -> Optional[LiveSignal]:
        try:
            return await LiveSignal.find_one({"signal_id": signal_id})
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch LiveSignal {signal_id}.", detail=str(exc)
            )

    async def get_for_symbol_and_date(
        self, symbol: str, trading_date: datetime
    ) -> Optional[LiveSignal]:
        """Return the (at most one) signal for (symbol, trading_date), or None."""
        try:
            return await LiveSignal.find_one(
                {"symbol": symbol.upper(), "trading_date": trading_date}
            )
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch LiveSignal for {symbol} on {trading_date.date()}.",
                detail=str(exc),
            )

    async def get_for_date(
        self,
        trading_date: datetime,
        status: Optional[LiveSignalStatus] = None,
    ) -> list[LiveSignal]:
        """Return all signals for a trading date; optionally filter by status."""
        try:
            query: dict = {"trading_date": trading_date}
            if status is not None:
                query["signal_status"] = status.value
            return (
                await LiveSignal.find(query)
                .sort("breakout_time")
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch LiveSignals for {trading_date.date()}.",
                detail=str(exc),
            )

    async def get_history_for_symbol(
        self,
        symbol: str,
        limit: int = 100,
        skip: int = 0,
    ) -> list[LiveSignal]:
        """Return live-signal history for a symbol, newest first."""
        try:
            return (
                await LiveSignal.find({"symbol": symbol.upper()})
                .sort("-trading_date")
                .skip(skip)
                .limit(limit)
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch LiveSignal history for {symbol}.", detail=str(exc)
            )

    async def list_recent(self, limit: int = 100, skip: int = 0) -> list[LiveSignal]:
        """Return the most recent signals across all symbols, newest first."""
        try:
            return (
                await LiveSignal.find({})
                .sort("-breakout_time")
                .skip(skip)
                .limit(limit)
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException(
                "Failed to list recent LiveSignals.", detail=str(exc)
            )

    async def count_for_date(self, trading_date: datetime) -> int:
        try:
            return await LiveSignal.find({"trading_date": trading_date}).count()
        except Exception as exc:
            raise DatabaseException(
                f"Failed to count LiveSignals for {trading_date.date()}.",
                detail=str(exc),
            )
