"""
Base types for health checks.

Every health check returns a `ComponentHealthResult` and optionally raises
`HealthCheckException` on unexpected failures (not component failure —
unexpected failure means the check itself crashed, which is different).

Component status values follow `ComponentStatus` in the model.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class ComponentHealthResult:
    """Outcome of running one health check."""

    component_name: str
    healthy: bool                        # True = green, False = red/yellow
    status: str                          # "healthy" | "degraded" | "unhealthy"
    latency_ms: float = 0.0
    error_count: int = 0
    error_message: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def ok(cls, component_name: str, latency_ms: float, **meta) -> "ComponentHealthResult":
        return cls(
            component_name=component_name,
            healthy=True,
            status="healthy",
            latency_ms=round(latency_ms, 2),
            metadata=meta,
        )

    @classmethod
    def degraded(
        cls,
        component_name: str,
        latency_ms: float,
        message: str,
        error_count: int = 1,
        **meta,
    ) -> "ComponentHealthResult":
        return cls(
            component_name=component_name,
            healthy=False,
            status="degraded",
            latency_ms=round(latency_ms, 2),
            error_count=error_count,
            error_message=message,
            metadata=meta,
        )

    @classmethod
    def unhealthy(
        cls,
        component_name: str,
        message: str,
        error_count: int = 1,
        **meta,
    ) -> "ComponentHealthResult":
        return cls(
            component_name=component_name,
            healthy=False,
            status="unhealthy",
            latency_ms=0.0,
            error_count=error_count,
            error_message=message,
            metadata=meta,
        )


# ── Base class ────────────────────────────────────────────────────────────────

class BaseHealthCheck(ABC):
    """
    Abstract base for all component health checks.

    Subclasses implement ``_run()`` which must return a `ComponentHealthResult`.
    The ``run()`` wrapper measures latency and catches unexpected exceptions so
    checks never propagate exceptions to the caller.
    """

    @property
    @abstractmethod
    def component_name(self) -> str:
        """Stable identifier for this component (e.g. 'mongodb', 'broker')."""

    @abstractmethod
    async def _run(self) -> ComponentHealthResult:
        """Execute the actual check logic. Override this in subclasses."""

    async def run(self) -> ComponentHealthResult:
        """
        Execute the check with latency measurement and exception isolation.

        Never raises — unexpected exceptions produce an ``unhealthy`` result.
        """
        t0 = time.perf_counter()
        try:
            result = await self._run()
            elapsed = (time.perf_counter() - t0) * 1000
            # Override latency with our measurement if check didn't set it.
            if result.latency_ms == 0.0:
                result = ComponentHealthResult(
                    component_name=result.component_name,
                    healthy=result.healthy,
                    status=result.status,
                    latency_ms=round(elapsed, 2),
                    error_count=result.error_count,
                    error_message=result.error_message,
                    metadata=result.metadata,
                    checked_at=result.checked_at,
                )
            return result
        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            return ComponentHealthResult(
                component_name=self.component_name,
                healthy=False,
                status="unhealthy",
                latency_ms=round(elapsed, 2),
                error_count=1,
                error_message=f"Check crashed: {exc}",
            )
