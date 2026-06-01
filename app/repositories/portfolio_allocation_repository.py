"""
Repository for PortfolioAllocation documents.

Follows the same pattern as all other repositories: raw MongoDB filter
dicts (Beanie 2.x), no ORM-style class attribute operators.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from app.core.exceptions import DatabaseException
from app.models.portfolio_allocation import AllocationStatus, PortfolioAllocation
from app.repositories.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class PortfolioAllocationRepository(BaseRepository[PortfolioAllocation]):
    document_model = PortfolioAllocation

    # ── Write ─────────────────────────────────────────────────────────────────

    async def upsert_by_signal_id(self, allocation: PortfolioAllocation) -> None:
        """Insert or replace the allocation row keyed by signal_id."""
        try:
            collection = PortfolioAllocation.get_motor_collection()
            doc = allocation.model_dump(mode="python")
            doc.pop("id", None)
            await collection.update_one(
                {"signal_id": allocation.signal_id},
                {"$set": doc},
                upsert=True,
            )
        except Exception as exc:
            raise DatabaseException(
                f"Failed to upsert portfolio allocation for signal {allocation.signal_id}",
                detail=str(exc),
            ) from exc

    # ── Reads ─────────────────────────────────────────────────────────────────

    async def get_by_signal_id(
        self, signal_id: str
    ) -> Optional[PortfolioAllocation]:
        try:
            return await PortfolioAllocation.find_one({"signal_id": signal_id})
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch portfolio allocation for signal {signal_id}",
                detail=str(exc),
            ) from exc

    async def get_for_date(
        self, trading_date: datetime
    ) -> list[PortfolioAllocation]:
        """Return all allocations for the given UTC-midnight trading date."""
        try:
            return (
                await PortfolioAllocation.find({"trading_date": trading_date})
                .sort("created_at")
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException(
                "Failed to fetch portfolio allocations for date",
                detail=str(exc),
            ) from exc

    async def get_approved_for_date(
        self, trading_date: datetime
    ) -> list[PortfolioAllocation]:
        """Return only APPROVED allocations for the session."""
        try:
            return (
                await PortfolioAllocation.find(
                    {
                        "trading_date": trading_date,
                        "allocation_status": AllocationStatus.APPROVED,
                    }
                )
                .sort("created_at")
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException(
                "Failed to fetch approved portfolio allocations",
                detail=str(exc),
            ) from exc

    async def get_for_date_range(
        self,
        from_date: datetime,
        to_date: datetime,
        status: Optional[AllocationStatus] = None,
    ) -> list[PortfolioAllocation]:
        """Return allocations within a date range, optionally filtered by status."""
        try:
            filt: dict = {
                "trading_date": {"$gte": from_date, "$lte": to_date}
            }
            if status is not None:
                filt["allocation_status"] = status
            return (
                await PortfolioAllocation.find(filt)
                .sort("trading_date")
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException(
                "Failed to fetch portfolio allocations for date range",
                detail=str(exc),
            ) from exc

    async def count_approved_for_date(self, trading_date: datetime) -> int:
        try:
            return await PortfolioAllocation.find(
                {
                    "trading_date": trading_date,
                    "allocation_status": AllocationStatus.APPROVED,
                }
            ).count()
        except Exception as exc:
            raise DatabaseException(
                "Failed to count approved portfolio allocations",
                detail=str(exc),
            ) from exc

    async def get_strategy_capital_for_date(
        self, trading_date: datetime, strategy_id: str
    ) -> float:
        """Sum of allocated_capital for APPROVED allocations of a strategy today."""
        try:
            allocations = await PortfolioAllocation.find(
                {
                    "trading_date": trading_date,
                    "strategy_id": strategy_id,
                    "allocation_status": AllocationStatus.APPROVED,
                }
            ).to_list()
            return round(sum(a.allocated_capital for a in allocations), 4)
        except Exception as exc:
            raise DatabaseException(
                f"Failed to compute strategy capital for {strategy_id}",
                detail=str(exc),
            ) from exc

    async def get_sector_capital_for_date(
        self, trading_date: datetime, sector: str
    ) -> float:
        """Sum of allocated_capital for APPROVED allocations in a sector today."""
        try:
            allocations = await PortfolioAllocation.find(
                {
                    "trading_date": trading_date,
                    "sector": sector,
                    "allocation_status": AllocationStatus.APPROVED,
                }
            ).to_list()
            return round(sum(a.allocated_capital for a in allocations), 4)
        except Exception as exc:
            raise DatabaseException(
                f"Failed to compute sector capital for {sector}",
                detail=str(exc),
            ) from exc

    async def count_correlated_for_date(
        self, trading_date: datetime, sector: str
    ) -> int:
        """Count open APPROVED positions in the same sector."""
        try:
            return await PortfolioAllocation.find(
                {
                    "trading_date": trading_date,
                    "sector": sector,
                    "allocation_status": AllocationStatus.APPROVED,
                }
            ).count()
        except Exception as exc:
            raise DatabaseException(
                f"Failed to count correlated positions for sector {sector}",
                detail=str(exc),
            ) from exc

    async def update_status(
        self,
        signal_id: str,
        new_status: AllocationStatus,
        *,
        rejection_reason: Optional[str] = None,
    ) -> None:
        """Transition the allocation status for a given signal_id."""
        try:
            update: dict = {
                "$set": {
                    "allocation_status": new_status,
                    "updated_at": datetime.utcnow(),
                }
            }
            if rejection_reason is not None:
                update["$set"]["rejection_reason"] = rejection_reason
            await PortfolioAllocation.find_one({"signal_id": signal_id}).update(update)
        except Exception as exc:
            raise DatabaseException(
                f"Failed to update allocation status for signal {signal_id}",
                detail=str(exc),
            ) from exc
