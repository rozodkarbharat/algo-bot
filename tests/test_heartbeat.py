"""
Unit tests for HeartbeatTracker.

All tests are synchronous-safe (run_coroutine_threadsafe) or async.
No DB, no external I/O.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.monitoring.heartbeat import HeartbeatRecord, HeartbeatTracker


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _fresh_tracker(*components: str) -> HeartbeatTracker:
    t = HeartbeatTracker()
    for c in components:
        t.register_component(c)
    return t


# ── register_component ────────────────────────────────────────────────────────

def test_register_adds_to_expected():
    t = _fresh_tracker("mongodb", "scheduler")
    assert "mongodb" in t.expected_components()
    assert "scheduler" in t.expected_components()


def test_register_idempotent():
    t = _fresh_tracker("comp")
    t.register_component("comp")
    t.register_component("comp")
    assert t.expected_components().count("comp") == 1


# ── record ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_record_heartbeat():
    t = _fresh_tracker("mongodb")
    await t.record("mongodb")
    rec = await t.get_record("mongodb")
    assert rec is not None
    assert rec.component_name == "mongodb"


@pytest.mark.asyncio
async def test_record_updates_timestamp():
    t = _fresh_tracker("mongodb")
    await t.record("mongodb")
    rec1 = await t.get_record("mongodb")
    await asyncio.sleep(0.01)
    await t.record("mongodb")
    rec2 = await t.get_record("mongodb")
    assert rec2.last_seen >= rec1.last_seen


@pytest.mark.asyncio
async def test_record_unknown_component_registers_it():
    t = HeartbeatTracker()
    await t.record("new_component")
    assert "new_component" in t.expected_components()


# ── report ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_report_never_seen():
    t = _fresh_tracker("missing_component")
    report = await t.report()
    assert "missing_component" in report.never_seen


@pytest.mark.asyncio
async def test_report_alive_after_heartbeat():
    t = _fresh_tracker("mongodb")
    await t.record("mongodb")
    report = await t.report()
    assert "mongodb" in report.alive
    assert "mongodb" not in report.stale
    assert "mongodb" not in report.never_seen


@pytest.mark.asyncio
async def test_report_stale_after_threshold():
    t = _fresh_tracker("old_comp")
    # Manually inject an old heartbeat
    async with t._lock:
        t._registry["old_comp"] = HeartbeatRecord(
            component_name="old_comp",
            last_seen=datetime.now(timezone.utc) - timedelta(seconds=300),
            stale_threshold_seconds=60,
        )

    report = await t.report()
    assert "old_comp" in report.stale


@pytest.mark.asyncio
async def test_report_all_healthy_flag():
    t = _fresh_tracker("a", "b")
    await t.record("a")
    await t.record("b")
    report = await t.report()
    assert report.all_healthy is True


@pytest.mark.asyncio
async def test_report_not_all_healthy_when_stale():
    t = _fresh_tracker("a", "b")
    await t.record("a")
    # "b" never heartbeated → never_seen
    report = await t.report()
    assert report.all_healthy is False


# ── HeartbeatRecord ───────────────────────────────────────────────────────────

def test_record_is_stale_when_old():
    rec = HeartbeatRecord(
        component_name="test",
        last_seen=datetime.now(timezone.utc) - timedelta(seconds=200),
        stale_threshold_seconds=60,
    )
    assert rec.is_stale is True


def test_record_not_stale_when_fresh():
    rec = HeartbeatRecord(
        component_name="test",
        last_seen=datetime.now(timezone.utc),
        stale_threshold_seconds=60,
    )
    assert rec.is_stale is False


def test_record_age_seconds_positive():
    rec = HeartbeatRecord(
        component_name="test",
        last_seen=datetime.now(timezone.utc) - timedelta(seconds=30),
        stale_threshold_seconds=60,
    )
    assert 0 < rec.age_seconds < 60
