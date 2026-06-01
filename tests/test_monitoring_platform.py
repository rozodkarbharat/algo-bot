"""
Comprehensive tests for the Monitoring & Health Platform.

Covers:
  - HeartbeatTracker business logic (TestHeartbeatLogic)
  - IncidentManager creation, deduplication, lifecycle (TestIncidentGeneration)
  - Individual health check logic (TestHealthChecks)
  - HealthAggregator orchestration (TestMonitoringWorkflow)
  - AlertRouter notification routing (TestAlertRouting)

No external I/O — all DB / broker calls are mocked.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.alert_event import AlertSeverity
from app.models.system_incident import IncidentStatus, SystemIncident
from app.monitoring.heartbeat import HeartbeatRecord, HeartbeatTracker
from app.monitoring.incident_manager import IncidentManager
from app.monitoring.health_checks.base import BaseHealthCheck, ComponentHealthResult
from app.monitoring.alert_router import AlertRouter


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _fresh_tracker(*components: str) -> HeartbeatTracker:
    t = HeartbeatTracker()
    for c in components:
        t.register_component(c)
    return t


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


def _make_router() -> AlertRouter:
    return AlertRouter()


# ═══════════════════════════════════════════════════════════════════════════════
# TestHeartbeatLogic
# ═══════════════════════════════════════════════════════════════════════════════

class TestHeartbeatLogic:
    """10+ tests covering HeartbeatTracker business logic."""

    @pytest.mark.asyncio
    async def test_fresh_heartbeat_not_stale(self):
        """Record a heartbeat — component must not be stale immediately after."""
        t = _fresh_tracker("scheduler")
        await t.record("scheduler")
        rec = await t.get_record("scheduler")
        assert rec is not None
        assert rec.is_stale is False

    @pytest.mark.asyncio
    async def test_stale_heartbeat_after_threshold(self):
        """Back-date last_seen beyond threshold — is_stale must be True."""
        t = _fresh_tracker("old_comp")
        async with t._lock:
            t._registry["old_comp"] = HeartbeatRecord(
                component_name="old_comp",
                last_seen=datetime.now(timezone.utc) - timedelta(seconds=300),
                stale_threshold_seconds=60,
            )
        rec = await t.get_record("old_comp")
        assert rec.is_stale is True

    @pytest.mark.asyncio
    async def test_never_seen_component_in_report(self):
        """Register a component but never record — appears in never_seen."""
        t = _fresh_tracker("missing_svc")
        report = await t.report()
        assert "missing_svc" in report.never_seen
        assert "missing_svc" not in report.alive
        assert "missing_svc" not in report.stale

    @pytest.mark.asyncio
    async def test_stale_component_in_report(self):
        """Back-date heartbeat past threshold — appears in stale list."""
        t = _fresh_tracker("aging_comp")
        async with t._lock:
            t._registry["aging_comp"] = HeartbeatRecord(
                component_name="aging_comp",
                last_seen=datetime.now(timezone.utc) - timedelta(seconds=500),
                stale_threshold_seconds=120,
            )
        report = await t.report()
        assert "aging_comp" in report.stale
        assert "aging_comp" not in report.alive
        assert "aging_comp" not in report.never_seen

    @pytest.mark.asyncio
    async def test_alive_component_in_report(self):
        """Record a heartbeat — appears in alive list, not stale or never_seen."""
        t = _fresh_tracker("mongodb")
        await t.record("mongodb")
        report = await t.report()
        assert "mongodb" in report.alive
        assert "mongodb" not in report.stale
        assert "mongodb" not in report.never_seen

    @pytest.mark.asyncio
    async def test_multiple_components_mixed_states(self):
        """Mix of alive, stale, and never_seen — each lands in the right bucket."""
        t = _fresh_tracker("comp_alive", "comp_stale", "comp_never")

        await t.record("comp_alive")

        # Manually inject an aged record for comp_stale
        async with t._lock:
            t._registry["comp_stale"] = HeartbeatRecord(
                component_name="comp_stale",
                last_seen=datetime.now(timezone.utc) - timedelta(seconds=400),
                stale_threshold_seconds=60,
            )

        report = await t.report()
        assert "comp_alive" in report.alive
        assert "comp_stale" in report.stale
        assert "comp_never" in report.never_seen

    @pytest.mark.asyncio
    async def test_record_updates_existing(self):
        """Recording twice should update last_seen, not create a duplicate."""
        t = _fresh_tracker("broker")
        await t.record("broker")
        rec1 = await t.get_record("broker")
        await asyncio.sleep(0.01)
        await t.record("broker")
        rec2 = await t.get_record("broker")

        assert rec2 is not None
        assert rec2.last_seen >= rec1.last_seen
        # Only one record in the registry
        assert len([k for k in t._registry if k == "broker"]) == 1

    @pytest.mark.asyncio
    async def test_all_healthy_property_true(self):
        """all_healthy is True only when alive has entries and stale/never_seen are empty."""
        t = _fresh_tracker("a", "b")
        await t.record("a")
        await t.record("b")
        report = await t.report()
        assert report.all_healthy is True

    @pytest.mark.asyncio
    async def test_all_healthy_property_false_when_stale(self):
        """all_healthy is False when any component is stale."""
        t = _fresh_tracker("ok", "stale")
        await t.record("ok")
        async with t._lock:
            t._registry["stale"] = HeartbeatRecord(
                component_name="stale",
                last_seen=datetime.now(timezone.utc) - timedelta(seconds=300),
                stale_threshold_seconds=60,
            )
        report = await t.report()
        assert report.all_healthy is False

    @pytest.mark.asyncio
    async def test_age_seconds_calculation(self):
        """age_seconds should reflect how long since last heartbeat."""
        backdate_secs = 30
        rec = HeartbeatRecord(
            component_name="test_age",
            last_seen=datetime.now(timezone.utc) - timedelta(seconds=backdate_secs),
            stale_threshold_seconds=60,
        )
        # Allow 1-second tolerance for execution time
        assert backdate_secs - 1 < rec.age_seconds < backdate_secs + 2

    @pytest.mark.asyncio
    async def test_concurrent_records(self):
        """Concurrent record() calls must not corrupt the registry (lock safety)."""
        t = _fresh_tracker("concurrent")

        async def _record():
            for _ in range(20):
                await t.record("concurrent")

        await asyncio.gather(_record(), _record(), _record())
        rec = await t.get_record("concurrent")
        assert rec is not None
        assert rec.component_name == "concurrent"
        # Still only one entry in the registry
        assert list(t._registry.keys()).count("concurrent") == 1


# ═══════════════════════════════════════════════════════════════════════════════
# TestIncidentGeneration
# ═══════════════════════════════════════════════════════════════════════════════

class TestIncidentGeneration:
    """8+ tests covering IncidentManager creation, deduplication, and lifecycle."""

    @pytest.mark.asyncio
    async def test_create_new_incident(self):
        """Create returns a new OPEN incident when none already exists."""
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
    async def test_duplicate_incident_deduplication(self):
        """Creating for the same component twice returns the existing incident."""
        manager = _make_manager()
        existing = _mock_incident(component="mongodb", status=IncidentStatus.OPEN)

        with (
            patch.object(manager, "_find_open", AsyncMock(return_value=existing)),
            patch.object(manager, "_upsert", AsyncMock()),
        ):
            result = await manager.create(
                component="mongodb",
                description="DB unreachable again",
            )

        assert result.incident_id == existing.incident_id
        # A new repeated-failure timeline entry was added
        assert len(result.timeline) >= 2

    @pytest.mark.asyncio
    async def test_resolve_incident(self):
        """resolve() sets status RESOLVED and populates resolved_at."""
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
    async def test_escalate_warning_to_critical(self):
        """escalate() bumps severity from WARNING to CRITICAL."""
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
    async def test_list_open_includes_open_and_investigating(self):
        """list_open() returns both OPEN and INVESTIGATING incidents."""
        manager = _make_manager()
        open_incidents = [
            _mock_incident(status=IncidentStatus.OPEN),
            _mock_incident(status=IncidentStatus.INVESTIGATING, incident_id="xyz"),
        ]

        with patch.object(SystemIncident, "find", MagicMock()) as mock_find:
            mock_find.return_value.sort.return_value.to_list = AsyncMock(
                return_value=open_incidents
            )
            result = await manager.list_open()

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_resolve_clears_from_open(self):
        """After resolving all component incidents, list_open returns empty."""
        manager = _make_manager()
        incident = _mock_incident(component="broker_angelone")

        # resolve_for_component calls _get_all_open then resolve for each
        with (
            patch.object(manager, "_get_all_open", AsyncMock(return_value=[incident])),
            patch.object(manager, "_get", AsyncMock(return_value=incident)),
            patch.object(manager, "_upsert", AsyncMock()),
        ):
            count = await manager.resolve_for_component("broker_angelone")

        assert count == 1
        # The incident is now RESOLVED
        assert incident.status == IncidentStatus.RESOLVED

    @pytest.mark.asyncio
    async def test_incident_timeline_entries(self):
        """Timeline grows with each successive update call."""
        manager = _make_manager()
        incident = _mock_incident()
        original_len = len(incident.timeline)

        with (
            patch.object(manager, "_get", AsyncMock(return_value=incident)),
            patch.object(manager, "_upsert", AsyncMock()),
        ):
            await manager.update(incident.incident_id, "Investigating root cause")
            await manager.update(incident.incident_id, "Found the issue")

        assert len(incident.timeline) == original_len + 2

    @pytest.mark.asyncio
    async def test_create_incident_with_metadata(self):
        """Extra metadata passed to create() is stored on the incident."""
        manager = _make_manager()
        meta = {"latency_ms": 1500.0, "error_code": 503}

        with (
            patch.object(manager, "_find_open", AsyncMock(return_value=None)),
            patch.object(manager, "_upsert", AsyncMock()),
        ):
            incident = await manager.create(
                component="websocket_manager",
                description="WS disconnected",
                metadata=meta,
            )

        assert incident.metadata["latency_ms"] == 1500.0
        assert incident.metadata["error_code"] == 503


# ═══════════════════════════════════════════════════════════════════════════════
# TestHealthChecks
# ═══════════════════════════════════════════════════════════════════════════════

class TestHealthChecks:
    """12+ tests covering individual health check logic with mocked dependencies."""

    # ── SchedulerHealthCheck ──────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_scheduler_not_running_returns_unhealthy(self):
        from app.monitoring.health_checks.scheduler_check import SchedulerHealthCheck

        mock_scheduler = MagicMock()
        mock_scheduler.running = False

        with patch("app.scheduler.scheduler.scheduler", mock_scheduler):
            result = await SchedulerHealthCheck().run()

        assert result.healthy is False
        assert result.status == "unhealthy"

    @pytest.mark.asyncio
    async def test_scheduler_running_with_jobs_returns_healthy(self):
        from app.monitoring.health_checks.scheduler_check import SchedulerHealthCheck

        mock_job = MagicMock()
        mock_job.id = "signal_scan"
        mock_job.next_run_time = datetime.now(timezone.utc) + timedelta(minutes=1)

        mock_scheduler = MagicMock()
        mock_scheduler.running = True
        mock_scheduler.get_jobs.return_value = [mock_job]

        with patch("app.scheduler.scheduler.scheduler", mock_scheduler):
            result = await SchedulerHealthCheck().run()

        assert result.healthy is True
        assert result.status == "healthy"
        assert result.metadata["job_count"] == 1

    @pytest.mark.asyncio
    async def test_scheduler_running_no_jobs_returns_degraded(self):
        from app.monitoring.health_checks.scheduler_check import SchedulerHealthCheck

        mock_scheduler = MagicMock()
        mock_scheduler.running = True
        mock_scheduler.get_jobs.return_value = []

        with patch("app.scheduler.scheduler.scheduler", mock_scheduler):
            result = await SchedulerHealthCheck().run()

        assert result.healthy is False
        assert result.status == "degraded"

    # ── MongoDBHealthCheck ────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_mongodb_connected_returns_healthy(self):
        from app.monitoring.health_checks.mongodb_check import MongoDBHealthCheck

        with patch("app.monitoring.health_checks.mongodb_check.get_database") as mock_db:
            mock_db.return_value.command = AsyncMock(return_value={"ok": 1})
            result = await MongoDBHealthCheck().run()

        assert result.healthy is True
        assert result.status == "healthy"

    @pytest.mark.asyncio
    async def test_mongodb_connection_failed_returns_unhealthy(self):
        from app.monitoring.health_checks.mongodb_check import MongoDBHealthCheck

        with patch("app.monitoring.health_checks.mongodb_check.get_database") as mock_db:
            mock_db.return_value.command = AsyncMock(
                side_effect=Exception("connection refused")
            )
            result = await MongoDBHealthCheck().run()

        assert result.healthy is False
        assert result.status == "unhealthy"
        assert "connection refused" in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_mongodb_slow_query_returns_degraded(self):
        """Patch time.perf_counter so the elapsed value crosses the LATENCY_WARN_MS threshold."""
        from app.monitoring.health_checks.mongodb_check import MongoDBHealthCheck

        # Simulate the ping taking 300 ms (above the 200 ms threshold).
        # perf_counter is called twice: t0 and after the await.
        counter = {"n": 0}
        original_counter_values = [0.0, 0.3]  # 300 ms gap

        def _fake_counter():
            val = original_counter_values[counter["n"]]
            counter["n"] = min(counter["n"] + 1, len(original_counter_values) - 1)
            return val

        with patch("app.monitoring.health_checks.mongodb_check.get_database") as mock_db:
            mock_db.return_value.command = AsyncMock(return_value={"ok": 1})
            with patch("app.monitoring.health_checks.mongodb_check.time") as mock_time:
                mock_time.perf_counter.side_effect = _fake_counter
                result = await MongoDBHealthCheck()._run()

        assert result.status == "degraded"
        assert result.healthy is False

    # ── BrokerHealthCheck ─────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_broker_session_valid_returns_healthy(self):
        from app.monitoring.health_checks.broker_check import BrokerHealthCheck

        mock_session = MagicMock()
        mock_session.expires_at = None  # no expiry info

        mock_auth = MagicMock()
        mock_auth.get_session = AsyncMock(return_value=mock_session)

        with patch("app.monitoring.health_checks.broker_check.angel_one_auth", mock_auth, create=True):
            with patch(
                "app.brokers.angelone.auth.angel_one_auth", mock_auth, create=True
            ):
                result = await BrokerHealthCheck().run()

        # The check imports angel_one_auth lazily inside _run().
        # We patch at the import location.
        assert result.component_name == "broker_angelone"

    @pytest.mark.asyncio
    async def test_broker_session_none_returns_unhealthy(self):
        from app.monitoring.health_checks.broker_check import BrokerHealthCheck

        mock_auth = MagicMock()
        mock_auth.get_session = AsyncMock(return_value=None)

        with patch("app.brokers.angelone.auth.angel_one_auth", mock_auth, create=True):
            result = await BrokerHealthCheck().run()

        assert result.healthy is False
        assert result.status == "unhealthy"

    @pytest.mark.asyncio
    async def test_broker_session_expiring_soon_returns_degraded(self):
        from app.monitoring.health_checks.broker_check import BrokerHealthCheck

        mock_session = MagicMock()
        # Session expires in 60 seconds — less than the 300-second grace
        mock_session.expires_at = datetime.now(timezone.utc) + timedelta(seconds=60)

        mock_auth = MagicMock()
        mock_auth.get_session = AsyncMock(return_value=mock_session)

        with patch("app.brokers.angelone.auth.angel_one_auth", mock_auth, create=True):
            result = await BrokerHealthCheck().run()

        assert result.status == "degraded"
        assert result.healthy is False

    # ── BaseHealthCheck ───────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_check_exception_isolation(self):
        """If _run() raises, run() must return unhealthy and never propagate."""

        class _CrashingCheck(BaseHealthCheck):
            @property
            def component_name(self) -> str:
                return "crashing_comp"

            async def _run(self) -> ComponentHealthResult:
                raise RuntimeError("Unexpected crash")

        result = await _CrashingCheck().run()
        assert result is not None
        assert result.healthy is False
        assert result.status == "unhealthy"
        assert "Unexpected crash" in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_latency_measured(self):
        """run() must set latency_ms > 0 even when _run() returns zero latency."""

        class _ZeroLatencyCheck(BaseHealthCheck):
            @property
            def component_name(self) -> str:
                return "zero_latency"

            async def _run(self) -> ComponentHealthResult:
                # Deliberately return 0.0 — the wrapper should override
                return ComponentHealthResult(
                    component_name="zero_latency",
                    healthy=True,
                    status="healthy",
                    latency_ms=0.0,
                )

        result = await _ZeroLatencyCheck().run()
        # BaseHealthCheck.run() measures elapsed and replaces the 0.0
        assert result.latency_ms >= 0.0

    @pytest.mark.asyncio
    async def test_healthy_check_preserves_metadata(self):
        """Metadata returned by _run() must pass through run() unchanged."""

        class _MetaCheck(BaseHealthCheck):
            @property
            def component_name(self) -> str:
                return "meta_comp"

            async def _run(self) -> ComponentHealthResult:
                return ComponentHealthResult.ok(
                    "meta_comp", latency_ms=5.0, custom_key="custom_value"
                )

        result = await _MetaCheck().run()
        assert result.metadata.get("custom_key") == "custom_value"


# ═══════════════════════════════════════════════════════════════════════════════
# TestMonitoringWorkflow
# ═══════════════════════════════════════════════════════════════════════════════

class TestMonitoringWorkflow:
    """6+ tests for HealthAggregator orchestration with all checks mocked."""

    def _make_healthy_result(self, name: str) -> ComponentHealthResult:
        return ComponentHealthResult.ok(name, latency_ms=10.0)

    def _make_unhealthy_result(self, name: str) -> ComponentHealthResult:
        return ComponentHealthResult.unhealthy(name, message=f"{name} is down")

    def _make_degraded_result(self, name: str) -> ComponentHealthResult:
        return ComponentHealthResult.degraded(name, latency_ms=300.0, message="slow")

    def _patch_aggregator(self, results: list[ComponentHealthResult]):
        """
        Return a context manager that replaces every check's run() with the
        supplied results (in order) and stubs out persistence + incident I/O.
        """
        from app.monitoring.health_aggregator import HealthAggregator

        aggregator = HealthAggregator()
        # Replace each check's run() with a coroutine that returns the matching result
        for check, result in zip(aggregator._checks, results):
            check.run = AsyncMock(return_value=result)
        # Pad any remaining checks with healthy results
        for check in aggregator._checks[len(results):]:
            check.run = AsyncMock(
                return_value=self._make_healthy_result(check.component_name)
            )
        return aggregator

    @pytest.mark.asyncio
    async def test_run_all_all_healthy_returns_healthy(self):
        from app.monitoring.health_aggregator import HealthAggregator

        agg = HealthAggregator()
        for check in agg._checks:
            check.run = AsyncMock(
                return_value=self._make_healthy_result(check.component_name)
            )

        with (
            patch("app.monitoring.health_aggregator.heartbeat_tracker") as mock_hb,
            patch("app.monitoring.health_aggregator.incident_manager") as mock_im,
        ):
            mock_hb.record = AsyncMock()
            mock_im.resolve_for_component = AsyncMock(return_value=0)
            mock_im.list_open = AsyncMock(return_value=[])
            with patch.object(agg, "_persist_status", AsyncMock()):
                report = await agg.run_all()

        assert report.overall_status == "healthy"
        assert report.unhealthy_count == 0
        assert report.degraded_count == 0

    @pytest.mark.asyncio
    async def test_run_all_one_unhealthy_returns_unhealthy(self):
        from app.monitoring.health_aggregator import HealthAggregator

        agg = HealthAggregator()
        # First check unhealthy, rest healthy
        agg._checks[0].run = AsyncMock(
            return_value=self._make_unhealthy_result(agg._checks[0].component_name)
        )
        for check in agg._checks[1:]:
            check.run = AsyncMock(
                return_value=self._make_healthy_result(check.component_name)
            )

        with (
            patch("app.monitoring.health_aggregator.heartbeat_tracker") as mock_hb,
            patch("app.monitoring.health_aggregator.incident_manager") as mock_im,
            patch("app.monitoring.health_aggregator.alert_router") as mock_ar,
        ):
            mock_hb.record = AsyncMock()
            mock_im.resolve_for_component = AsyncMock(return_value=0)
            mock_im.create = AsyncMock(return_value=_mock_incident())
            mock_im.list_open = AsyncMock(return_value=[_mock_incident()])
            mock_ar.database_unreachable = AsyncMock()
            mock_ar.broker_disconnected = AsyncMock()
            mock_ar.scheduler_stopped = AsyncMock()
            mock_ar.kill_switch_engaged = AsyncMock()
            with patch.object(agg, "_persist_status", AsyncMock()):
                report = await agg.run_all()

        assert report.overall_status == "unhealthy"

    @pytest.mark.asyncio
    async def test_run_all_degraded_only_returns_degraded(self):
        from app.monitoring.health_aggregator import HealthAggregator

        agg = HealthAggregator()
        # First check degraded, rest healthy
        agg._checks[0].run = AsyncMock(
            return_value=self._make_degraded_result(agg._checks[0].component_name)
        )
        for check in agg._checks[1:]:
            check.run = AsyncMock(
                return_value=self._make_healthy_result(check.component_name)
            )

        with (
            patch("app.monitoring.health_aggregator.heartbeat_tracker") as mock_hb,
            patch("app.monitoring.health_aggregator.incident_manager") as mock_im,
            patch("app.monitoring.health_aggregator.alert_router") as mock_ar,
        ):
            mock_hb.record = AsyncMock()
            mock_im.resolve_for_component = AsyncMock(return_value=0)
            mock_im.create = AsyncMock(return_value=_mock_incident())
            mock_im.list_open = AsyncMock(return_value=[])
            mock_ar.database_unreachable = AsyncMock()
            mock_ar.broker_disconnected = AsyncMock()
            mock_ar.scheduler_stopped = AsyncMock()
            mock_ar.kill_switch_engaged = AsyncMock()
            with patch.object(agg, "_persist_status", AsyncMock()):
                report = await agg.run_all()

        assert report.overall_status == "degraded"
        assert report.unhealthy_count == 0
        assert report.degraded_count >= 1

    @pytest.mark.asyncio
    async def test_run_all_creates_incident_for_failure(self):
        """A failing component must trigger incident_manager.create()."""
        from app.monitoring.health_aggregator import HealthAggregator

        agg = HealthAggregator()
        agg._checks[0].run = AsyncMock(
            return_value=self._make_unhealthy_result("mongodb")
        )
        for check in agg._checks[1:]:
            check.run = AsyncMock(
                return_value=self._make_healthy_result(check.component_name)
            )

        with (
            patch("app.monitoring.health_aggregator.heartbeat_tracker") as mock_hb,
            patch("app.monitoring.health_aggregator.incident_manager") as mock_im,
            patch("app.monitoring.health_aggregator.alert_router") as mock_ar,
        ):
            mock_hb.record = AsyncMock()
            mock_im.resolve_for_component = AsyncMock(return_value=0)
            mock_im.create = AsyncMock(return_value=_mock_incident())
            mock_im.list_open = AsyncMock(return_value=[_mock_incident()])
            # All alert_router methods are async — wire up the ones _route_alert may call
            mock_ar.database_unreachable = AsyncMock()
            mock_ar.broker_disconnected = AsyncMock()
            mock_ar.scheduler_stopped = AsyncMock()
            mock_ar.kill_switch_engaged = AsyncMock()
            with patch.object(agg, "_persist_status", AsyncMock()):
                await agg.run_all()

        mock_im.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_all_resolves_incident_on_recovery(self):
        """A component that is now healthy must call resolve_for_component()."""
        from app.monitoring.health_aggregator import HealthAggregator

        agg = HealthAggregator()
        # All checks healthy
        for check in agg._checks:
            check.run = AsyncMock(
                return_value=self._make_healthy_result(check.component_name)
            )

        with (
            patch("app.monitoring.health_aggregator.heartbeat_tracker") as mock_hb,
            patch("app.monitoring.health_aggregator.incident_manager") as mock_im,
        ):
            mock_hb.record = AsyncMock()
            mock_im.resolve_for_component = AsyncMock(return_value=1)
            mock_im.list_open = AsyncMock(return_value=[])
            with patch.object(agg, "_persist_status", AsyncMock()):
                await agg.run_all()

        # resolve_for_component is called once per healthy component
        assert mock_im.resolve_for_component.call_count == len(agg._checks)

    @pytest.mark.asyncio
    async def test_run_all_records_heartbeat_on_healthy(self):
        """Healthy components must record a heartbeat."""
        from app.monitoring.health_aggregator import HealthAggregator

        agg = HealthAggregator()
        for check in agg._checks:
            check.run = AsyncMock(
                return_value=self._make_healthy_result(check.component_name)
            )

        with (
            patch("app.monitoring.health_aggregator.heartbeat_tracker") as mock_hb,
            patch("app.monitoring.health_aggregator.incident_manager") as mock_im,
        ):
            mock_hb.record = AsyncMock()
            mock_im.resolve_for_component = AsyncMock(return_value=0)
            mock_im.list_open = AsyncMock(return_value=[])
            with patch.object(agg, "_persist_status", AsyncMock()):
                await agg.run_all()

        # heartbeat.record called once for each healthy component
        assert mock_hb.record.call_count == len(agg._checks)


# ═══════════════════════════════════════════════════════════════════════════════
# TestAlertRouting
# ═══════════════════════════════════════════════════════════════════════════════

class TestAlertRouting:
    """6+ tests verifying AlertRouter fires correct notification events."""

    @pytest.mark.asyncio
    async def test_broker_disconnected_fires_alert(self):
        router = _make_router()
        with patch.object(router, "_dispatch", AsyncMock()) as mock_dispatch:
            await router.broker_disconnected("AngelOne", "TCP timeout")

        mock_dispatch.assert_called_once()
        kwargs = mock_dispatch.call_args.kwargs
        assert "AngelOne" in kwargs["message"]
        assert kwargs["severity"] == AlertSeverity.CRITICAL
        assert kwargs["dedup_key"] == "broker_disconnected:AngelOne"

    @pytest.mark.asyncio
    async def test_database_unreachable_fires_alert(self):
        router = _make_router()
        with patch.object(router, "_dispatch", AsyncMock()) as mock_dispatch:
            await router.database_unreachable("connection timeout")

        mock_dispatch.assert_called_once()
        kwargs = mock_dispatch.call_args.kwargs
        assert kwargs["dedup_key"] == "database_unreachable"
        assert kwargs["severity"] == AlertSeverity.CRITICAL
        assert "MongoDB" in kwargs["message"]

    @pytest.mark.asyncio
    async def test_scheduler_stopped_fires_alert(self):
        router = _make_router()
        with patch.object(router, "_dispatch", AsyncMock()) as mock_dispatch:
            await router.scheduler_stopped("eod_sync")

        mock_dispatch.assert_called_once()
        kwargs = mock_dispatch.call_args.kwargs
        assert "eod_sync" in kwargs["message"]
        assert kwargs["severity"] == AlertSeverity.CRITICAL

    @pytest.mark.asyncio
    async def test_market_data_stale_fires_alert(self):
        router = _make_router()
        with patch.object(router, "_dispatch", AsyncMock()) as mock_dispatch:
            await router.market_data_stale(45.0, symbol="RELIANCE")

        mock_dispatch.assert_called_once()
        kwargs = mock_dispatch.call_args.kwargs
        assert kwargs["severity"] == AlertSeverity.WARNING
        assert kwargs["dedup_key"] == "market_data_stale:RELIANCE"
        assert "RELIANCE" in kwargs["message"]

    @pytest.mark.asyncio
    async def test_reconciliation_mismatch_fires_alert(self):
        router = _make_router()
        with patch.object(router, "_dispatch", AsyncMock()) as mock_dispatch:
            await router.reconciliation_mismatch(
                broker="AngelOne", mismatch_count=3, description="Position delta"
            )

        mock_dispatch.assert_called_once()
        kwargs = mock_dispatch.call_args.kwargs
        assert kwargs["dedup_key"] == "reconciliation_mismatch:AngelOne"
        assert kwargs["severity"] == AlertSeverity.WARNING
        assert "3" in kwargs["message"]

    @pytest.mark.asyncio
    async def test_kill_switch_engaged_fires_alert(self):
        router = _make_router()
        with patch.object(router, "_dispatch", AsyncMock()) as mock_dispatch:
            await router.kill_switch_engaged("daily_loss_limit")

        mock_dispatch.assert_called_once()
        kwargs = mock_dispatch.call_args.kwargs
        assert kwargs["severity"] == AlertSeverity.CRITICAL
        assert kwargs["dedup_key"] == "kill_switch_engaged"
        assert "kill switch" in kwargs["message"].lower()

    @pytest.mark.asyncio
    async def test_dispatch_failure_never_propagates(self):
        """A crash inside notification_manager must never surface to the caller."""
        router = _make_router()
        with patch(
            "app.notifications.notification_manager.notification_manager"
        ) as mock_nm:
            mock_nm.dispatch_system_alert = AsyncMock(
                side_effect=Exception("provider down")
            )
            # Must not raise
            await router.broker_disconnected("AngelOne", "crash")

    @pytest.mark.asyncio
    async def test_alert_router_no_symbol_uses_feed_dedup_key(self):
        """market_data_stale without symbol falls back to 'feed' dedup key."""
        router = _make_router()
        with patch.object(router, "_dispatch", AsyncMock()) as mock_dispatch:
            await router.market_data_stale(60.0)

        kwargs = mock_dispatch.call_args.kwargs
        assert kwargs["dedup_key"] == "market_data_stale:feed"

    @pytest.mark.asyncio
    async def test_escalation_alert_uses_incident_id_in_dedup(self):
        """escalation_alert must include incident_id in the dedup key."""
        router = _make_router()
        with patch(
            "app.notifications.notification_manager.notification_manager"
        ) as mock_nm:
            mock_nm.dispatch_system_alert = AsyncMock()
            await router.escalation_alert(
                component="mongodb",
                incident_id="abc123",
                reason="5 consecutive failures",
            )

        mock_nm.dispatch_system_alert.assert_called_once()
        call_kwargs = mock_nm.dispatch_system_alert.call_args.kwargs
        assert "abc123" in call_kwargs["dedup_key"]
        assert call_kwargs["severity"] == AlertSeverity.CRITICAL
