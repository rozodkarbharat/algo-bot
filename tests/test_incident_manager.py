"""
Unit tests for IncidentManager.

All MongoDB I/O is mocked — tests verify the lifecycle logic,
deduplication, timeline entries, and escalation routing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.alert_event import AlertSeverity
from app.models.system_incident import IncidentStatus, SystemIncident
from app.monitoring.incident_manager import IncidentManager


def _mock_incident(
    incident_id: str = "abc123",
    component: str = "mongodb",
    severity: AlertSeverity = AlertSeverity.WARNING,
    status: IncidentStatus = IncidentStatus.OPEN,
) -> SystemIncident:
    now = datetime.now(timezone.utc)
    return SystemIncident.model_construct(
        incident_id=incident_id,
        severity=severity,
        component=component,
        description="Test incident",
        detected_at=now,
        resolved_at=None,
        status=status,
        timeline=[{"at": now.isoformat(), "message": "Opened"}],
        metadata={},
        created_at=now,
        updated_at=now,
    )


def _make_manager() -> IncidentManager:
    return IncidentManager()


# ── create ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_new_incident():
    manager = _make_manager()

    with (
        patch.object(manager, "_find_open", AsyncMock(return_value=None)),
        patch.object(manager, "_upsert", AsyncMock()),
    ):
        incident = await manager.create(
            component="mongodb",
            description="DB unreachable",
            severity=AlertSeverity.CRITICAL,
        )

    assert incident.component == "mongodb"
    assert incident.severity == AlertSeverity.CRITICAL
    assert incident.status == IncidentStatus.OPEN
    assert len(incident.timeline) >= 1


@pytest.mark.asyncio
async def test_create_returns_existing_open_incident():
    manager = _make_manager()
    existing = _mock_incident(component="mongodb")

    with (
        patch.object(manager, "_find_open", AsyncMock(return_value=existing)),
        patch.object(manager, "_upsert", AsyncMock()),
    ):
        result = await manager.create(
            component="mongodb",
            description="DB unreachable again",
        )

    assert result.incident_id == existing.incident_id
    assert len(result.timeline) >= 2   # added repeated failure entry


@pytest.mark.asyncio
async def test_create_adds_timeline_entry():
    manager = _make_manager()

    with (
        patch.object(manager, "_find_open", AsyncMock(return_value=None)),
        patch.object(manager, "_upsert", AsyncMock()),
    ):
        incident = await manager.create("broker_angelone", "Disconnected")

    assert any("Incident opened" in e["message"] for e in incident.timeline)


# ── update ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_adds_message():
    manager = _make_manager()
    incident = _mock_incident()

    with (
        patch.object(manager, "_get", AsyncMock(return_value=incident)),
        patch.object(manager, "_upsert", AsyncMock()),
    ):
        result = await manager.update(incident.incident_id, "Investigating root cause")

    assert any("Investigating" in e["message"] for e in result.timeline)


@pytest.mark.asyncio
async def test_update_changes_status():
    manager = _make_manager()
    incident = _mock_incident()

    with (
        patch.object(manager, "_get", AsyncMock(return_value=incident)),
        patch.object(manager, "_upsert", AsyncMock()),
    ):
        result = await manager.update(
            incident.incident_id,
            "Now investigating",
            status=IncidentStatus.INVESTIGATING,
        )

    assert result.status == IncidentStatus.INVESTIGATING


@pytest.mark.asyncio
async def test_update_returns_none_for_missing_incident():
    manager = _make_manager()
    with patch.object(manager, "_get", AsyncMock(return_value=None)):
        result = await manager.update("nonexistent", "message")
    assert result is None


# ── resolve ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_sets_resolved_status():
    manager = _make_manager()
    incident = _mock_incident()

    with (
        patch.object(manager, "_get", AsyncMock(return_value=incident)),
        patch.object(manager, "_upsert", AsyncMock()),
    ):
        result = await manager.resolve(incident.incident_id, "DB recovered.")

    assert result.status == IncidentStatus.RESOLVED
    assert result.resolved_at is not None


@pytest.mark.asyncio
async def test_resolve_adds_timeline_entry():
    manager = _make_manager()
    incident = _mock_incident()

    with (
        patch.object(manager, "_get", AsyncMock(return_value=incident)),
        patch.object(manager, "_upsert", AsyncMock()),
    ):
        result = await manager.resolve(incident.incident_id, "Component came back.")

    assert any("Resolved" in e["message"] for e in result.timeline)


# ── resolve_for_component ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_for_component_resolves_all():
    manager = _make_manager()
    incidents = [_mock_incident(incident_id=f"i{i}") for i in range(3)]

    with patch.object(manager, "_get_all_open", AsyncMock(return_value=incidents)):
        with patch.object(manager, "resolve", AsyncMock(return_value=incidents[0])) as mock_resolve:
            count = await manager.resolve_for_component("mongodb")

    assert count == 3
    assert mock_resolve.call_count == 3


# ── escalate ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_escalate_bumps_to_critical():
    manager = _make_manager()
    incident = _mock_incident(severity=AlertSeverity.WARNING)

    with (
        patch.object(manager, "_get", AsyncMock(return_value=incident)),
        patch.object(manager, "_upsert", AsyncMock()),
        patch("app.monitoring.alert_router.alert_router") as mock_router,
    ):
        mock_router.escalation_alert = AsyncMock()
        result = await manager.escalate(incident.incident_id, "Too many failures")

    assert result.severity == AlertSeverity.CRITICAL
    mock_router.escalation_alert.assert_called_once()


@pytest.mark.asyncio
async def test_escalate_returns_none_for_missing():
    manager = _make_manager()
    with patch.object(manager, "_get", AsyncMock(return_value=None)):
        result = await manager.escalate("missing", "reason")
    assert result is None


# ── list_open ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_open_returns_incidents():
    manager = _make_manager()
    from app.models.system_incident import IncidentStatus as IS
    open_incidents = [_mock_incident(status=IS.OPEN), _mock_incident(status=IS.INVESTIGATING, incident_id="xyz")]

    with patch.object(SystemIncident, "find", MagicMock()) as mock_find:
        mock_find.return_value.sort.return_value.to_list = AsyncMock(return_value=open_incidents)
        result = await manager.list_open()

    assert len(result) == 2
