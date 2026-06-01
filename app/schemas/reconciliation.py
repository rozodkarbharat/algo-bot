"""
Request / response schemas for the broker reconciliation API.
"""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.models.broker_reconciliation import (
    DiscrepancyStatus,
    DiscrepancyType,
    ReconciliationRunStatus,
)
from app.models.alert_event import AlertSeverity


class ReconciliationRunResponse(BaseModel):
    run_id: str
    broker_name: str
    started_at: datetime
    completed_at: Optional[datetime]
    status: ReconciliationRunStatus
    discrepancies_found: int
    orders_checked: int
    positions_checked: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class DiscrepancyResponse(BaseModel):
    discrepancy_id: str
    run_id: str
    discrepancy_type: DiscrepancyType
    symbol: Optional[str]
    severity: AlertSeverity
    broker_value: Optional[Any]
    internal_value: Optional[Any]
    description: str
    status: DiscrepancyStatus
    detected_at: datetime
    resolved_at: Optional[datetime]
    auto_resolution_attempted: bool
    metadata: dict[str, Any] = Field(default_factory=dict)


class TriggerReconciliationRequest(BaseModel):
    broker_name: str = Field(default="AngelOne", description="Broker to reconcile against")
    trigger: str = Field(default="manual", description="Trigger source label")


class TriggerReconciliationResponse(BaseModel):
    run_id: str
    status: ReconciliationRunStatus
    discrepancies_found: int
    orders_checked: int
    positions_checked: int
    message: str
