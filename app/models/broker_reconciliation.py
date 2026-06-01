"""
Broker reconciliation models.

BrokerReconciliationRun  — one record per reconciliation cycle.
BrokerDiscrepancy        — individual mismatch detected during a run.

Lifecycle:
  Run:         RUNNING → COMPLETED | FAILED
  Discrepancy: DETECTED → AUTO_RESOLVED | RESOLVED | IGNORED

Collections:
  broker_reconciliation_runs
  broker_discrepancies
"""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Optional
from uuid import uuid4

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from app.models.alert_event import AlertSeverity


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_run_id() -> str:
    return uuid4().hex[:16]


def _new_discrepancy_id() -> str:
    return uuid4().hex[:16]


# ── Run status ────────────────────────────────────────────────────────────────

class ReconciliationRunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# ── Discrepancy taxonomy ──────────────────────────────────────────────────────

class DiscrepancyType(StrEnum):
    # Order discrepancies
    MISSING_ORDER = "missing_order"       # DB order not found at broker
    ORPHAN_ORDER = "orphan_order"         # Broker order has no internal record
    DUPLICATE_ORDER = "duplicate_order"   # Same broker_order_id on multiple DB rows
    STATUS_MISMATCH = "status_mismatch"   # Internal status != broker status
    REJECTED_ORDER = "rejected_order"     # Broker rejected the order
    PARTIAL_FILL = "partial_fill"         # Partial fill with stale filled_quantity
    STALE_ORDER = "stale_order"           # Non-terminal order older than threshold
    # Position discrepancies
    QUANTITY_MISMATCH = "quantity_mismatch"  # DB qty != broker qty
    PRICE_MISMATCH = "price_mismatch"        # Average price differs beyond tolerance
    MISSING_POSITION = "missing_position"    # Open DB position absent from broker
    ORPHAN_POSITION = "orphan_position"      # Broker position has no internal record
    # Stop-loss discrepancies
    MISSING_STOP_LOSS = "missing_stop_loss"  # Open position has no valid SL configured


class DiscrepancyStatus(StrEnum):
    DETECTED = "detected"
    AUTO_RESOLVED = "auto_resolved"
    RESOLVED = "resolved"
    IGNORED = "ignored"


# Canonical severity per discrepancy type — used for alerting and incident triage.
DISCREPANCY_SEVERITY: dict[DiscrepancyType, AlertSeverity] = {
    DiscrepancyType.MISSING_STOP_LOSS: AlertSeverity.CRITICAL,
    DiscrepancyType.ORPHAN_POSITION: AlertSeverity.CRITICAL,
    DiscrepancyType.REJECTED_ORDER: AlertSeverity.WARNING,
    DiscrepancyType.MISSING_POSITION: AlertSeverity.WARNING,
    DiscrepancyType.ORPHAN_ORDER: AlertSeverity.WARNING,
    DiscrepancyType.QUANTITY_MISMATCH: AlertSeverity.WARNING,
    DiscrepancyType.STATUS_MISMATCH: AlertSeverity.WARNING,
    DiscrepancyType.MISSING_ORDER: AlertSeverity.WARNING,
    DiscrepancyType.DUPLICATE_ORDER: AlertSeverity.WARNING,
    DiscrepancyType.PARTIAL_FILL: AlertSeverity.WARNING,
    DiscrepancyType.PRICE_MISMATCH: AlertSeverity.INFO,
    DiscrepancyType.STALE_ORDER: AlertSeverity.INFO,
}


# ── Documents ─────────────────────────────────────────────────────────────────

class BrokerReconciliationRun(Document):
    """
    One complete reconciliation cycle against a broker.

    Collection: broker_reconciliation_runs
    """

    run_id: str = Field(default_factory=_new_run_id)
    broker_name: str = Field(..., description="e.g. 'AngelOne'")

    started_at: datetime = Field(default_factory=_utcnow)
    completed_at: Optional[datetime] = Field(default=None)

    status: ReconciliationRunStatus = Field(default=ReconciliationRunStatus.RUNNING)

    discrepancies_found: int = Field(default=0)
    orders_checked: int = Field(default=0)
    positions_checked: int = Field(default=0)

    # Stores error messages, run context, or trigger source
    metadata: dict[str, Any] = Field(default_factory=dict)

    class Settings:
        name = "broker_reconciliation_runs"
        indexes = [
            IndexModel([("run_id", ASCENDING)], unique=True, name="run_id_unique"),
            IndexModel([("broker_name", ASCENDING)]),
            IndexModel([("status", ASCENDING)]),
            IndexModel([("started_at", DESCENDING)]),
        ]


class BrokerDiscrepancy(Document):
    """
    Individual mismatch detected during a reconciliation run.

    Collection: broker_discrepancies
    """

    discrepancy_id: str = Field(default_factory=_new_discrepancy_id)
    run_id: str = Field(..., description="Parent BrokerReconciliationRun.run_id")

    discrepancy_type: DiscrepancyType
    symbol: Optional[str] = Field(default=None)
    severity: AlertSeverity

    # Raw values that differed
    broker_value: Optional[Any] = Field(
        default=None, description="Value observed at broker side"
    )
    internal_value: Optional[Any] = Field(
        default=None, description="Value found in our DB"
    )

    description: str = Field(default="")

    status: DiscrepancyStatus = Field(default=DiscrepancyStatus.DETECTED)

    detected_at: datetime = Field(default_factory=_utcnow)
    resolved_at: Optional[datetime] = Field(default=None)

    # True once an auto-resolution action has been attempted (regardless of outcome)
    auto_resolution_attempted: bool = Field(default=False)

    # order_id / position_id / broker_order_id for traceability
    metadata: dict[str, Any] = Field(default_factory=dict)

    class Settings:
        name = "broker_discrepancies"
        indexes = [
            IndexModel(
                [("discrepancy_id", ASCENDING)],
                unique=True,
                name="discrepancy_id_unique",
            ),
            IndexModel([("run_id", ASCENDING)]),
            IndexModel([("discrepancy_type", ASCENDING)]),
            IndexModel([("severity", ASCENDING)]),
            IndexModel([("status", ASCENDING)]),
            IndexModel([("symbol", ASCENDING)], sparse=True),
            IndexModel([("detected_at", DESCENDING)]),
        ]
