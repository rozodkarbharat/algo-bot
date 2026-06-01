"""
Strategy catalog models: StrategyCatalog, StrategyVersion, StrategyDeployment.

StrategyCatalog is the source of truth for every strategy in the research lab.
StrategyVersion stores immutable parameter snapshots per semver tag.
StrategyDeployment records every status transition for a full audit trail.
"""

import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Optional

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StrategyStatus(StrEnum):
    DEVELOPMENT = "DEVELOPMENT"
    TESTING = "TESTING"
    PAPER = "PAPER"
    LIVE = "LIVE"
    RETIRED = "RETIRED"


VALID_TRANSITIONS: dict[StrategyStatus, list[StrategyStatus]] = {
    StrategyStatus.DEVELOPMENT: [StrategyStatus.TESTING, StrategyStatus.RETIRED],
    StrategyStatus.TESTING: [StrategyStatus.PAPER, StrategyStatus.DEVELOPMENT, StrategyStatus.RETIRED],
    StrategyStatus.PAPER: [StrategyStatus.LIVE, StrategyStatus.TESTING, StrategyStatus.RETIRED],
    StrategyStatus.LIVE: [StrategyStatus.RETIRED],
    StrategyStatus.RETIRED: [],
}


class StrategyCatalog(Document):
    catalog_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    strategy_id: str
    strategy_name: str
    current_version: str = Field(default="1.0.0")
    status: StrategyStatus = Field(default=StrategyStatus.DEVELOPMENT)
    description: str = Field(default="")
    category: str = Field(default="")
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    metadata: dict = Field(default_factory=dict)

    class Settings:
        name = "strategy_catalog"
        indexes = [
            IndexModel([("catalog_id", ASCENDING)], unique=True, name="catalog_id_unique"),
            IndexModel([("strategy_id", ASCENDING)], unique=True, name="strategy_id_unique"),
            IndexModel([("status", ASCENDING)], name="catalog_status"),
            IndexModel([("created_at", DESCENDING)], name="catalog_created_at"),
        ]


class StrategyVersion(Document):
    version_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    catalog_id: str
    strategy_id: str
    version: str
    parameters: dict = Field(default_factory=dict)
    change_notes: str = Field(default="")
    created_by: str = Field(default="system")
    created_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "strategy_versions"
        indexes = [
            IndexModel([("version_id", ASCENDING)], unique=True, name="version_id_unique"),
            IndexModel(
                [("catalog_id", ASCENDING), ("version", ASCENDING)],
                unique=True,
                name="catalog_version_unique",
            ),
            IndexModel([("catalog_id", ASCENDING)], name="version_catalog_id"),
        ]


class StrategyDeployment(Document):
    deployment_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    catalog_id: str
    strategy_id: str
    from_status: Optional[StrategyStatus] = None
    to_status: StrategyStatus
    version: str
    approved_by: str = Field(default="system")
    notes: str = Field(default="")
    deployed_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "strategy_deployments"
        indexes = [
            IndexModel([("deployment_id", ASCENDING)], unique=True, name="deployment_id_unique"),
            IndexModel([("catalog_id", ASCENDING)], name="deployment_catalog_id"),
            IndexModel([("deployed_at", DESCENDING)], name="deployment_deployed_at"),
            IndexModel([("strategy_id", ASCENDING)], name="deployment_strategy_id"),
        ]
