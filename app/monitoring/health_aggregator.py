"""
Health aggregator — runs all component checks, persists results, and
orchestrates the heartbeat + incident lifecycle.

This is the single entry point the scheduler calls every 60 seconds.
It:
  1. Runs all 7 component health checks concurrently.
  2. Records a heartbeat for each passing component.
  3. Persists one SystemHealthStatus document per component (upsert).
  4. Opens an incident for any component that transitions to unhealthy.
  5. Resolves open incidents for components that recover.
  6. Returns an AggregateHealthReport for the ops API.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.models.alert_event import AlertSeverity
from app.models.system_health_status import ComponentStatus, SystemHealthStatus
from app.monitoring.health_checks.base import ComponentHealthResult
from app.monitoring.health_checks.broker_check import BrokerHealthCheck
from app.monitoring.health_checks.execution_check import ExecutionHealthCheck
from app.monitoring.health_checks.mongodb_check import MongoDBHealthCheck
from app.monitoring.health_checks.portfolio_check import PortfolioHealthCheck
from app.monitoring.health_checks.scheduler_check import SchedulerHealthCheck
from app.monitoring.health_checks.signal_engine_check import SignalEngineHealthCheck
from app.monitoring.health_checks.paper_trading_check import PaperTradingHealthCheck
from app.monitoring.health_checks.reconciliation_check import ReconciliationHealthCheck
from app.monitoring.health_checks.websocket_check import WebSocketHealthCheck
from app.monitoring.heartbeat import heartbeat_tracker
from app.monitoring.incident_manager import incident_manager
from app.monitoring.alert_router import alert_router
from app.utils.logger import get_logger
from app.utils.market_time import now_utc

logger = get_logger(__name__)

# How many consecutive errors before a WARNING incident is auto-escalated
ESCALATION_ERROR_THRESHOLD = 5


@dataclass
class AggregateHealthReport:
    """Full platform health snapshot from one check cycle."""

    overall_status: str            # "healthy" | "degraded" | "unhealthy"
    components: list[ComponentHealthResult] = field(default_factory=list)
    open_incident_count: int = 0
    generated_at: datetime = field(default_factory=now_utc)

    @property
    def healthy_count(self) -> int:
        return sum(1 for c in self.components if c.status == "healthy")

    @property
    def unhealthy_count(self) -> int:
        return sum(1 for c in self.components if c.status == "unhealthy")

    @property
    def degraded_count(self) -> int:
        return sum(1 for c in self.components if c.status == "degraded")


# Persistent error count per component (in-memory; resets on app restart)
_consecutive_errors: dict[str, int] = {}


class HealthAggregator:
    """
    Runs all health checks, manages incidents, and persists state.
    """

    def __init__(self) -> None:
        self._checks = [
            MongoDBHealthCheck(),
            BrokerHealthCheck(),
            WebSocketHealthCheck(),
            SchedulerHealthCheck(),
            SignalEngineHealthCheck(),
            PortfolioHealthCheck(),
            ExecutionHealthCheck(),
            PaperTradingHealthCheck(),
            ReconciliationHealthCheck(),
        ]

    async def run_all(self) -> AggregateHealthReport:
        """
        Run all checks concurrently and return the aggregate report.

        Never raises — individual check failures are caught inside
        BaseHealthCheck.run().
        """
        results = await asyncio.gather(
            *[check.run() for check in self._checks],
            return_exceptions=False,
        )

        # Process each result
        for result in results:
            await self._process_result(result)

        # Determine overall status
        statuses = [r.status for r in results]
        if any(s == "unhealthy" for s in statuses):
            overall = "unhealthy"
        elif any(s == "degraded" for s in statuses):
            overall = "degraded"
        else:
            overall = "healthy"

        open_incidents = await incident_manager.list_open()
        return AggregateHealthReport(
            overall_status=overall,
            components=list(results),
            open_incident_count=len(open_incidents),
        )

    # ── Result processing ─────────────────────────────────────────────────────

    async def _process_result(self, result: ComponentHealthResult) -> None:
        name = result.component_name

        # Update heartbeat
        if result.healthy:
            await heartbeat_tracker.record(name)

        # Persist SystemHealthStatus
        await self._persist_status(result)

        # Incident lifecycle
        if result.healthy:
            _consecutive_errors[name] = 0
            await incident_manager.resolve_for_component(name)
        else:
            count = _consecutive_errors.get(name, 0) + 1
            _consecutive_errors[name] = count

            severity = (
                AlertSeverity.CRITICAL
                if result.status == "unhealthy"
                else AlertSeverity.WARNING
            )
            incident = await incident_manager.create(
                component=name,
                description=result.error_message or f"{name} is {result.status}",
                severity=severity,
                metadata=result.metadata,
            )

            # Auto-escalate repeated failures
            if (
                count >= ESCALATION_ERROR_THRESHOLD
                and incident
                and incident.severity != AlertSeverity.CRITICAL
            ):
                await incident_manager.escalate(
                    incident.incident_id,
                    reason=f"Failed {count} consecutive health checks.",
                )

            # Specific alert routing
            await self._route_alert(result)

    async def _route_alert(self, result: ComponentHealthResult) -> None:
        """Fire specific alert types based on component + status."""
        name = result.component_name
        if name == "mongodb" and result.status == "unhealthy":
            await alert_router.database_unreachable(result.error_message or "")
        elif name == "broker_angelone" and result.status == "unhealthy":
            await alert_router.broker_disconnected(
                "AngelOne", result.error_message or ""
            )
        elif name == "scheduler" and result.status == "unhealthy":
            await alert_router.scheduler_stopped()
        elif name == "execution_engine":
            kill_engaged = result.metadata.get("kill_switch_engaged", False)
            if kill_engaged:
                await alert_router.kill_switch_engaged(
                    result.metadata.get("kill_switch_reason") or "manual"
                )

    async def _persist_status(self, result: ComponentHealthResult) -> None:
        """Upsert one SystemHealthStatus document for this component."""
        try:
            from app.repositories.system_health_status_repository import (
                SystemHealthStatusRepository,
            )
            _status_map = {
                "healthy": ComponentStatus.HEALTHY,
                "degraded": ComponentStatus.DEGRADED,
                "unhealthy": ComponentStatus.UNHEALTHY,
            }
            comp_status = _status_map.get(result.status, ComponentStatus.UNKNOWN)
            now = now_utc()
            doc = SystemHealthStatus.model_construct(
                component_name=result.component_name,
                status=comp_status,
                last_heartbeat=now if result.healthy else None,
                latency_ms=result.latency_ms,
                error_count=_consecutive_errors.get(result.component_name, 0),
                error_message=result.error_message,
                metadata=result.metadata,
                created_at=now,
                updated_at=now,
            )
            repo = SystemHealthStatusRepository()
            await repo.upsert(doc)
        except Exception as exc:
            logger.warning("[health-agg] persist_status failed for %s: %s", result.component_name, exc)


# ── Module-level singleton ────────────────────────────────────────────────────

health_aggregator = HealthAggregator()
