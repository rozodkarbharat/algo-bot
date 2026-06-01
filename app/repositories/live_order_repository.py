"""
LiveOrder repository — data-access layer for the live_orders collection.

Repositories wrap all Beanie/Motor I/O so services remain DB-agnostic. Uses
raw MongoDB filter dicts (Beanie 2.x requirement).

Duplicate-prevention strategy:
  - The unique index (signal_id, broker_name) is the engine's primary
    idempotency guarantee. `insert_idempotent()` translates MongoDB
    DuplicateKeyError into the typed `DuplicateLiveOrderException` so
    callers can treat duplicate suppression as an expected outcome.
"""

from datetime import datetime
from typing import Optional

from pymongo import ReplaceOne
from pymongo.errors import DuplicateKeyError

from app.core.exceptions import DatabaseException, DuplicateLiveOrderException
from app.models.live_order import LiveOrder, LiveOrderStatus
from app.repositories.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class LiveOrderRepository(BaseRepository[LiveOrder]):
    document_model = LiveOrder

    # ── Writes ────────────────────────────────────────────────────────────────

    async def insert_idempotent(self, order: LiveOrder) -> LiveOrder:
        """
        Insert a fresh LiveOrder, raising DuplicateLiveOrderException when
        the unique (signal_id, broker_name) index fires.
        """
        try:
            return await order.insert()
        except DuplicateKeyError:
            logger.info(
                "Duplicate live order blocked: signal=%s broker=%s",
                order.signal_id, order.broker_name,
            )
            raise DuplicateLiveOrderException(
                identifier=f"signal={order.signal_id} broker={order.broker_name}",
                detail={
                    "signal_id": order.signal_id,
                    "broker_name": order.broker_name,
                },
            )
        except Exception as exc:
            logger.error("insert_idempotent failed for %s: %s", order.symbol, exc, exc_info=True)
            raise DatabaseException("Failed to insert LiveOrder.", detail=str(exc))

    async def upsert_by_order_id(self, order: LiveOrder) -> LiveOrder:
        """Replace the document keyed by order_id, or insert if absent."""
        try:
            order.mark_updated()
            collection = LiveOrder.get_pymongo_collection()
            doc = order.model_dump(exclude={"id"})
            await collection.update_one(
                {"order_id": order.order_id},
                {"$set": doc},
                upsert=True,
            )
            return order
        except Exception as exc:
            logger.error("Upsert LiveOrder failed for %s: %s", order.order_id, exc)
            raise DatabaseException(
                f"Failed to upsert LiveOrder {order.order_id}.", detail=str(exc)
            )

    async def bulk_upsert(self, orders: list[LiveOrder]) -> int:
        if not orders:
            return 0
        try:
            collection = LiveOrder.get_pymongo_collection()
            ops = [
                ReplaceOne(
                    {"order_id": o.order_id},
                    o.model_dump(exclude={"id"}),
                    upsert=True,
                )
                for o in orders
            ]
            result = await collection.bulk_write(ops, ordered=False)
            return result.upserted_count + result.modified_count
        except Exception as exc:
            logger.error("Bulk upsert LiveOrder failed: %s", exc, exc_info=True)
            raise DatabaseException("Bulk upsert of LiveOrder failed.", detail=str(exc))

    # ── Reads ─────────────────────────────────────────────────────────────────

    async def get_by_order_id(self, order_id: str) -> Optional[LiveOrder]:
        try:
            return await LiveOrder.find_one({"order_id": order_id})
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch LiveOrder {order_id}.", detail=str(exc)
            )

    async def get_by_broker_order_id(self, broker_order_id: str) -> Optional[LiveOrder]:
        try:
            return await LiveOrder.find_one({"broker_order_id": broker_order_id})
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch LiveOrder by broker_order_id={broker_order_id}.",
                detail=str(exc),
            )

    async def get_by_signal_and_broker(
        self, signal_id: str, broker_name: str
    ) -> Optional[LiveOrder]:
        try:
            return await LiveOrder.find_one(
                {"signal_id": signal_id, "broker_name": broker_name}
            )
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch LiveOrder by signal={signal_id}.", detail=str(exc)
            )

    async def get_non_terminal(
        self, broker_name: Optional[str] = None
    ) -> list[LiveOrder]:
        """Return every order that has not yet reached a terminal state."""
        terminal = [
            LiveOrderStatus.FILLED.value,
            LiveOrderStatus.CANCELLED.value,
            LiveOrderStatus.REJECTED.value,
        ]
        query: dict = {"order_status": {"$nin": terminal}}
        if broker_name is not None:
            query["broker_name"] = broker_name
        try:
            return (
                await LiveOrder.find(query)
                .sort("created_at")
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException(
                "Failed to fetch non-terminal live orders.", detail=str(exc)
            )

    async def get_for_date(self, trading_date: datetime) -> list[LiveOrder]:
        try:
            return (
                await LiveOrder.find({"trading_date": trading_date})
                .sort("created_at")
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch live orders for {trading_date.date()}.",
                detail=str(exc),
            )

    async def list_recent(self, limit: int = 100, skip: int = 0) -> list[LiveOrder]:
        try:
            return (
                await LiveOrder.find({})
                .sort("-created_at")
                .skip(skip)
                .limit(limit)
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException("Failed to list live orders.", detail=str(exc))

    async def count_for_date_in_statuses(
        self, trading_date: datetime, statuses: list[LiveOrderStatus]
    ) -> int:
        """Count orders in any of the given statuses for a trading date."""
        try:
            query = {
                "trading_date": trading_date,
                "order_status": {"$in": [s.value for s in statuses]},
            }
            return await LiveOrder.find(query).count()
        except Exception as exc:
            raise DatabaseException(
                f"Failed to count live orders for {trading_date.date()}.",
                detail=str(exc),
            )
