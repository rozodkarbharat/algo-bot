"""
Broker reconciliation repository — data-access layer for reconciliation collections.

Two repositories:
  BrokerReconciliationRunRepository   — CRUD for broker_reconciliation_runs
  BrokerDiscrepancyRepository         — CRUD for broker_discrepancies
"""

from typing import Optional

from app.core.exceptions import DatabaseException
from app.models.broker_reconciliation import (
    BrokerDiscrepancy,
    BrokerReconciliationRun,
    DiscrepancyStatus,
    DiscrepancyType,
    ReconciliationRunStatus,
)
from app.repositories.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class BrokerReconciliationRunRepository(BaseRepository[BrokerReconciliationRun]):
    document_model = BrokerReconciliationRun

    async def upsert(self, run: BrokerReconciliationRun) -> BrokerReconciliationRun:
        try:
            collection = BrokerReconciliationRun.get_motor_collection()
            doc = run.model_dump(exclude={"id"})
            await collection.update_one(
                {"run_id": run.run_id}, {"$set": doc}, upsert=True
            )
            return run
        except Exception as exc:
            logger.error("Upsert reconciliation run %s failed: %s", run.run_id, exc)
            raise DatabaseException(
                f"Failed to upsert reconciliation run {run.run_id}.", detail=str(exc)
            )

    async def get_by_run_id(
        self, run_id: str
    ) -> Optional[BrokerReconciliationRun]:
        try:
            return await BrokerReconciliationRun.find_one({"run_id": run_id})
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch reconciliation run {run_id}.", detail=str(exc)
            )

    async def list_recent(
        self, limit: int = 20, skip: int = 0
    ) -> list[BrokerReconciliationRun]:
        try:
            return (
                await BrokerReconciliationRun.find({})
                .sort("-started_at")
                .skip(skip)
                .limit(limit)
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException(
                "Failed to list reconciliation runs.", detail=str(exc)
            )

    async def get_latest_completed(self) -> Optional[BrokerReconciliationRun]:
        try:
            return await BrokerReconciliationRun.find_one(
                {"status": ReconciliationRunStatus.COMPLETED.value},
                sort=[("started_at", -1)],
            )
        except Exception as exc:
            raise DatabaseException(
                "Failed to fetch latest completed run.", detail=str(exc)
            )


class BrokerDiscrepancyRepository(BaseRepository[BrokerDiscrepancy]):
    document_model = BrokerDiscrepancy

    async def upsert(self, discrepancy: BrokerDiscrepancy) -> BrokerDiscrepancy:
        try:
            collection = BrokerDiscrepancy.get_motor_collection()
            doc = discrepancy.model_dump(exclude={"id"})
            await collection.update_one(
                {"discrepancy_id": discrepancy.discrepancy_id},
                {"$set": doc},
                upsert=True,
            )
            return discrepancy
        except Exception as exc:
            logger.error(
                "Upsert discrepancy %s failed: %s", discrepancy.discrepancy_id, exc
            )
            raise DatabaseException(
                f"Failed to upsert discrepancy {discrepancy.discrepancy_id}.",
                detail=str(exc),
            )

    async def list_discrepancies(
        self,
        run_id: Optional[str] = None,
        status: Optional[DiscrepancyStatus] = None,
        symbol: Optional[str] = None,
        discrepancy_type: Optional[DiscrepancyType] = None,
        limit: int = 100,
        skip: int = 0,
    ) -> list[BrokerDiscrepancy]:
        query: dict = {}
        if run_id:
            query["run_id"] = run_id
        if status:
            query["status"] = status.value
        if symbol:
            query["symbol"] = symbol
        if discrepancy_type:
            query["discrepancy_type"] = discrepancy_type.value
        try:
            return (
                await BrokerDiscrepancy.find(query)
                .sort("-detected_at")
                .skip(skip)
                .limit(limit)
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException(
                "Failed to list broker discrepancies.", detail=str(exc)
            )

    async def count_detected(self) -> int:
        """Count all unresolved discrepancies across all runs."""
        try:
            return await BrokerDiscrepancy.find(
                {"status": DiscrepancyStatus.DETECTED.value}
            ).count()
        except Exception as exc:
            raise DatabaseException(
                "Failed to count detected discrepancies.", detail=str(exc)
            )
