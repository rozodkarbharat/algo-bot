"""
Repositories for StrategyCatalog, StrategyVersion, and StrategyDeployment documents.

Raw-dict query pattern — no ORM-style Beanie field expressions (Beanie 2.x / Pydantic v2).
Each repository extends BaseRepository[DocT] and provides domain-specific query methods.
"""

from datetime import datetime, timezone
from typing import Optional

from app.models.strategy_catalog import (
    StrategyCatalog,
    StrategyDeployment,
    StrategyStatus,
    StrategyVersion,
)
from app.repositories.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class StrategyCatalogRepository(BaseRepository[StrategyCatalog]):
    """Repository for StrategyCatalog documents."""

    document_model = StrategyCatalog

    # ── Reads ─────────────────────────────────────────────────────────────────

    async def get_by_catalog_id(self, catalog_id: str) -> Optional[StrategyCatalog]:
        """Return the catalog entry matching *catalog_id*, or None."""
        return await StrategyCatalog.find_one({"catalog_id": catalog_id})

    async def get_by_strategy_id(self, strategy_id: str) -> Optional[StrategyCatalog]:
        """Return the catalog entry matching *strategy_id*, or None."""
        return await StrategyCatalog.find_one({"strategy_id": strategy_id})

    async def get_by_status(self, status: StrategyStatus) -> list[StrategyCatalog]:
        """Return all catalog entries with the given *status*."""
        return await StrategyCatalog.find({"status": status.value}).to_list()

    async def list_all(self, skip: int = 0, limit: int = 100) -> list[StrategyCatalog]:
        """Return a paginated list of all catalog entries, newest first."""
        return (
            await StrategyCatalog.find({})
            .sort("-created_at")
            .skip(skip)
            .limit(limit)
            .to_list()
        )

    # ── Writes ────────────────────────────────────────────────────────────────

    async def update_status(
        self, catalog_id: str, status: StrategyStatus
    ) -> Optional[StrategyCatalog]:
        """
        Set *status* on the catalog entry identified by *catalog_id*.

        Also stamps *updated_at* to the current UTC time.
        Returns the updated document, or None if not found.
        """
        doc = await self.get_by_catalog_id(catalog_id)
        if doc is None:
            logger.warning(
                "update_status: catalog_id=%s not found", catalog_id
            )
            return None

        doc.status = status
        doc.updated_at = datetime.now(timezone.utc)
        return await self.save(doc)


class StrategyVersionRepository(BaseRepository[StrategyVersion]):
    """Repository for StrategyVersion documents."""

    document_model = StrategyVersion

    # ── Reads ─────────────────────────────────────────────────────────────────

    async def get_by_version_id(self, version_id: str) -> Optional[StrategyVersion]:
        """Return the version snapshot matching *version_id*, or None."""
        return await StrategyVersion.find_one({"version_id": version_id})

    async def get_by_catalog_id(self, catalog_id: str) -> list[StrategyVersion]:
        """Return all versions for *catalog_id*, sorted newest first."""
        return (
            await StrategyVersion.find({"catalog_id": catalog_id})
            .sort("-created_at")
            .to_list()
        )

    async def get_latest_for_catalog(
        self, catalog_id: str
    ) -> Optional[StrategyVersion]:
        """Return the most recently created version for *catalog_id*, or None."""
        results = (
            await StrategyVersion.find({"catalog_id": catalog_id})
            .sort("-created_at")
            .limit(1)
            .to_list()
        )
        return results[0] if results else None

    async def get_by_version(
        self, catalog_id: str, version: str
    ) -> Optional[StrategyVersion]:
        """Return the specific *version* snapshot for *catalog_id*, or None."""
        return await StrategyVersion.find_one(
            {"catalog_id": catalog_id, "version": version}
        )


class StrategyDeploymentRepository(BaseRepository[StrategyDeployment]):
    """Repository for StrategyDeployment documents."""

    document_model = StrategyDeployment

    # ── Reads ─────────────────────────────────────────────────────────────────

    async def get_by_deployment_id(
        self, deployment_id: str
    ) -> Optional[StrategyDeployment]:
        """Return the deployment record matching *deployment_id*, or None."""
        return await StrategyDeployment.find_one({"deployment_id": deployment_id})

    async def get_by_catalog_id(
        self, catalog_id: str, limit: int = 50
    ) -> list[StrategyDeployment]:
        """Return deployment records for *catalog_id*, most recent first."""
        return (
            await StrategyDeployment.find({"catalog_id": catalog_id})
            .sort("-deployed_at")
            .limit(limit)
            .to_list()
        )

    async def get_by_strategy_id(self, strategy_id: str) -> list[StrategyDeployment]:
        """Return all deployment records for *strategy_id*."""
        return (
            await StrategyDeployment.find({"strategy_id": strategy_id})
            .sort("-deployed_at")
            .to_list()
        )
