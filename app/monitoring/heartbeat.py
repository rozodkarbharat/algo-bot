"""
Heartbeat tracker — detects stale / silent components.

Components register a heartbeat by calling ``record(component_name)``.
The tracker compares the last heartbeat time against a configurable
staleness threshold and returns a structured staleness report.

The scheduler calls ``run_all()`` every 60 seconds to ping each known
component and update the in-memory registry.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.utils.logger import get_logger
from app.utils.market_time import now_utc

logger = get_logger(__name__)

# Default threshold: a component is considered stale after this many seconds
DEFAULT_STALE_THRESHOLD_SECONDS: int = 120


@dataclass
class HeartbeatRecord:
    """One entry in the in-memory heartbeat registry."""

    component_name: str
    last_seen: datetime
    stale_threshold_seconds: int = DEFAULT_STALE_THRESHOLD_SECONDS

    @property
    def is_stale(self) -> bool:
        age = (now_utc() - self.last_seen).total_seconds()
        return age > self.stale_threshold_seconds

    @property
    def age_seconds(self) -> float:
        return max(0.0, (now_utc() - self.last_seen).total_seconds())


@dataclass
class HeartbeatReport:
    """Snapshot of the heartbeat registry."""

    alive: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    never_seen: list[str] = field(default_factory=list)
    records: dict[str, HeartbeatRecord] = field(default_factory=dict)
    generated_at: datetime = field(default_factory=now_utc)

    @property
    def all_healthy(self) -> bool:
        return not self.stale and not self.never_seen


class HeartbeatTracker:
    """
    In-process heartbeat registry.

    Thread-safe through asyncio.Lock — all mutations go through the lock.
    """

    def __init__(self) -> None:
        self._registry: dict[str, HeartbeatRecord] = {}
        self._expected: set[str] = set()
        self._lock: asyncio.Lock = asyncio.Lock()

    # ── Public API ────────────────────────────────────────────────────────────

    def register_component(
        self,
        component_name: str,
        stale_threshold_seconds: int = DEFAULT_STALE_THRESHOLD_SECONDS,
    ) -> None:
        """
        Declare a component that should be heartbeated periodically.

        Components not yet heartbeated will appear in ``never_seen``.
        """
        self._expected.add(component_name)
        if component_name not in self._registry:
            logger.debug("[heartbeat] registered component: %s", component_name)

    async def record(self, component_name: str) -> None:
        """Record a heartbeat from a component (call from the component itself)."""
        async with self._lock:
            if component_name in self._registry:
                self._registry[component_name].last_seen = now_utc()
            else:
                self._registry[component_name] = HeartbeatRecord(
                    component_name=component_name,
                    last_seen=now_utc(),
                )
            self._expected.add(component_name)

    async def report(self) -> HeartbeatReport:
        """Build and return the current heartbeat snapshot."""
        async with self._lock:
            alive = []
            stale = []
            never_seen = []

            for name in self._expected:
                rec = self._registry.get(name)
                if rec is None:
                    never_seen.append(name)
                elif rec.is_stale:
                    stale.append(name)
                else:
                    alive.append(name)

            return HeartbeatReport(
                alive=sorted(alive),
                stale=sorted(stale),
                never_seen=sorted(never_seen),
                records=dict(self._registry),
            )

    async def get_record(self, component_name: str) -> Optional[HeartbeatRecord]:
        async with self._lock:
            return self._registry.get(component_name)

    def expected_components(self) -> list[str]:
        return sorted(self._expected)


# ── Module-level singleton ────────────────────────────────────────────────────

heartbeat_tracker = HeartbeatTracker()

# Pre-register the known components so they show up in ``never_seen`` from boot.
_KNOWN_COMPONENTS = [
    "mongodb",
    "broker_angelone",
    "websocket_manager",
    "scheduler",
    "signal_engine",
    "portfolio_engine",
    "execution_engine",
    "paper_trading_engine",
    "reconciliation_engine",
]
for _c in _KNOWN_COMPONENTS:
    heartbeat_tracker.register_component(_c)
