"""
Repository for PortfolioRiskState documents.

One document per trading day — upserted atomically on every allocation event.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from app.core.exceptions import DatabaseException
from app.models.portfolio_risk_state import PortfolioRiskState
from app.repositories.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class PortfolioRiskStateRepository(BaseRepository[PortfolioRiskState]):
    document_model = PortfolioRiskState

    async def get_for_date(
        self, trading_date: datetime
    ) -> Optional[PortfolioRiskState]:
        """Return the risk state document for the given UTC-midnight date."""
        try:
            return await PortfolioRiskState.find_one({"trading_date": trading_date})
        except Exception as exc:
            raise DatabaseException(
                "Failed to fetch portfolio risk state",
                detail=str(exc),
            ) from exc

    async def upsert(self, state: PortfolioRiskState) -> None:
        """Atomically insert or replace the risk state document for the date."""
        try:
            collection = PortfolioRiskState.get_motor_collection()
            doc = state.model_dump(mode="python")
            doc.pop("id", None)
            await collection.update_one(
                {"trading_date": state.trading_date},
                {"$set": doc},
                upsert=True,
            )
        except Exception as exc:
            raise DatabaseException(
                "Failed to upsert portfolio risk state",
                detail=str(exc),
            ) from exc

    async def get_latest(self) -> Optional[PortfolioRiskState]:
        """Return the most recently updated risk state document."""
        try:
            return (
                await PortfolioRiskState.find()
                .sort("-trading_date")
                .limit(1)
                .first_or_none()
            )
        except Exception as exc:
            raise DatabaseException(
                "Failed to fetch latest portfolio risk state",
                detail=str(exc),
            ) from exc
