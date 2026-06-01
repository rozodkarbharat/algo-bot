"""
Incident manager — lifecycle management for operational incidents.

An incident is created when a health check detects a failure that requires
operator attention. The lifecycle is:

  OPEN → INVESTIGATING → RESOLVED

Escalation:
  - When a WARNING incident becomes CRITICAL (repeated failures), it is
    escalated: severity updated, immediate notification sent (bypass dedup).

The manager is the ONLY writer for SystemIncident documents.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

from app.models.alert_event import AlertSeverity
from app.models.system_incident import IncidentStatus, SystemIncident, _new_incident_id
from app.utils.logger import get_logger
from app.utils.market_time import now_utc

logger = get_logger(__name__)


class IncidentManager:
    """
    Manages incident lifecycle: create, update, resolve, escalate.

    Designed as a module-level singleton.  All writes use model_construct()
    + Motor upsert to avoid Beanie collection-init checks in unit tests.
    """

    def __init__(self) -> None:
        self._lock: asyncio.Lock = asyncio.Lock()

    # ── Public API ────────────────────────────────────────────────────────────

    async def create(
        self,
        component: str,
        description: str,
        severity: AlertSeverity = AlertSeverity.WARNING,
        metadata: Optional[dict] = None,
    ) -> SystemIncident:
        """
        Open a new incident.

        Deduplicates: if an OPEN/INVESTIGATING incident for the same component
        already exists with the same severity, returns the existing one.
        """
        async with self._lock:
            existing = await self._find_open(component)
            if existing:
                existing.add_timeline_entry(f"Repeated failure: {description}")
                await self._upsert(existing)
                logger.info(
                    "[incident] updated existing incident %s for %s",
                    existing.incident_id, component,
                )
                return existing

            now = now_utc()
            incident = SystemIncident.model_construct(
                incident_id=_new_incident_id(),
                severity=severity,
                component=component,
                description=description,
                detected_at=now,
                resolved_at=None,
                status=IncidentStatus.OPEN,
                timeline=[{"at": now.isoformat(), "message": f"Incident opened: {description}"}],
                metadata=metadata or {},
                created_at=now,
                updated_at=now,
            )
            await self._upsert(incident)
            logger.warning(
                "[incident] created %s severity=%s component=%s",
                incident.incident_id, severity, component,
            )
            return incident

    async def update(
        self, incident_id: str, message: str, status: Optional[IncidentStatus] = None
    ) -> Optional[SystemIncident]:
        """Add a timeline entry and optionally change status."""
        incident = await self._get(incident_id)
        if incident is None:
            logger.warning("[incident] update: incident %s not found", incident_id)
            return None
        incident.add_timeline_entry(message)
        if status:
            incident.status = status
        await self._upsert(incident)
        return incident

    async def acknowledge(
        self, incident_id: str, message: str = "Acknowledged."
    ) -> Optional[SystemIncident]:
        """
        Acknowledge an OPEN incident — operator has seen it and is working on it.

        Transitions OPEN → ACKNOWLEDGED.  ACKNOWLEDGED incidents remain visible in
        the ops dashboard and continue to fire escalations if still unresolved.
        """
        incident = await self._get(incident_id)
        if incident is None:
            logger.warning("[incident] acknowledge: incident %s not found", incident_id)
            return None
        if incident.status == IncidentStatus.RESOLVED:
            logger.warning("[incident] acknowledge: incident %s already resolved", incident_id)
            return incident
        incident.status = IncidentStatus.ACKNOWLEDGED
        incident.add_timeline_entry(f"Acknowledged: {message}")
        await self._upsert(incident)
        logger.info("[incident] acknowledged %s for %s", incident_id, incident.component)
        return incident

    async def resolve(
        self, incident_id: str, resolution_message: str = "Resolved."
    ) -> Optional[SystemIncident]:
        """Mark an incident resolved."""
        incident = await self._get(incident_id)
        if incident is None:
            return None
        now = now_utc()
        incident.status = IncidentStatus.RESOLVED
        incident.resolved_at = now
        incident.add_timeline_entry(f"Resolved: {resolution_message}")
        await self._upsert(incident)
        logger.info(
            "[incident] resolved %s for %s", incident_id, incident.component
        )
        return incident

    async def resolve_for_component(self, component: str) -> int:
        """Resolve all open incidents for a component. Returns count resolved."""
        open_incidents = await self._get_all_open(component)
        for incident in open_incidents:
            await self.resolve(incident.incident_id, "Auto-resolved: component recovered.")
        return len(open_incidents)

    async def escalate(
        self, incident_id: str, reason: str = "Escalated due to repeated failures."
    ) -> Optional[SystemIncident]:
        """
        Escalate an incident to CRITICAL.

        Fires an immediate notification (bypass throttle) via the alert router.
        """
        incident = await self._get(incident_id)
        if incident is None:
            return None
        incident.severity = AlertSeverity.CRITICAL
        incident.add_timeline_entry(f"Escalated: {reason}")
        await self._upsert(incident)

        # Immediate out-of-band notification (bypass throttle window)
        try:
            from app.monitoring.alert_router import alert_router
            await alert_router.escalation_alert(
                component=incident.component,
                incident_id=incident_id,
                reason=reason,
            )
        except Exception as exc:
            logger.error("[incident] escalation notification failed: %s", exc)

        logger.critical(
            "[incident] ESCALATED %s component=%s", incident_id, incident.component
        )
        return incident

    async def list_open(self, component: Optional[str] = None) -> list[SystemIncident]:
        """Return all OPEN, ACKNOWLEDGED, or INVESTIGATING incidents."""
        try:
            query: dict = {
                "status": {"$in": [
                    IncidentStatus.OPEN,
                    IncidentStatus.ACKNOWLEDGED,
                    IncidentStatus.INVESTIGATING,
                ]}
            }
            if component:
                query["component"] = component
            return (
                await SystemIncident.find(query).sort("-detected_at").to_list()
            )
        except Exception as exc:
            logger.error("[incident] list_open failed: %s", exc)
            return []

    async def list_recent(self, limit: int = 50) -> list[SystemIncident]:
        """Return the most recent incidents of any status."""
        try:
            return (
                await SystemIncident.find({}).sort("-detected_at").limit(limit).to_list()
            )
        except Exception as exc:
            logger.error("[incident] list_recent failed: %s", exc)
            return []

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _find_open(self, component: str) -> Optional[SystemIncident]:
        try:
            return await SystemIncident.find_one(
                {
                    "component": component,
                    "status": {"$in": [IncidentStatus.OPEN, IncidentStatus.INVESTIGATING]},
                }
            )
        except Exception:
            return None

    async def _get_all_open(self, component: str) -> list[SystemIncident]:
        try:
            return await SystemIncident.find(
                {
                    "component": component,
                    "status": {"$in": [IncidentStatus.OPEN, IncidentStatus.INVESTIGATING]},
                }
            ).to_list()
        except Exception:
            return []

    async def _get(self, incident_id: str) -> Optional[SystemIncident]:
        try:
            return await SystemIncident.find_one({"incident_id": incident_id})
        except Exception:
            return None

    async def _upsert(self, incident: SystemIncident) -> None:
        try:
            collection = SystemIncident.get_pymongo_collection()
            doc = incident.model_dump(mode="python")
            doc.pop("id", None)
            await collection.update_one(
                {"incident_id": incident.incident_id},
                {"$set": doc},
                upsert=True,
            )
        except Exception as exc:
            logger.error("[incident] upsert failed: %s", exc)


# ── Module-level singleton ────────────────────────────────────────────────────

incident_manager = IncidentManager()
