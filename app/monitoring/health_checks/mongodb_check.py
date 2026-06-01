"""MongoDB health check — ping + latency measurement."""

from __future__ import annotations

import time

from app.database.mongodb import get_database
from app.monitoring.health_checks.base import BaseHealthCheck, ComponentHealthResult
from app.utils.logger import get_logger

logger = get_logger(__name__)

LATENCY_WARN_MS = 200.0   # mark degraded if ping RTT exceeds this


class MongoDBHealthCheck(BaseHealthCheck):
    """Verify MongoDB is reachable and measure ping latency."""

    @property
    def component_name(self) -> str:
        return "mongodb"

    async def _run(self) -> ComponentHealthResult:
        t0 = time.perf_counter()
        try:
            db = get_database()
            await db.command("ping")
            latency_ms = (time.perf_counter() - t0) * 1000

            if latency_ms > LATENCY_WARN_MS:
                return ComponentHealthResult.degraded(
                    self.component_name,
                    latency_ms=latency_ms,
                    message=f"MongoDB ping latency high: {latency_ms:.1f}ms",
                    latency_ms_threshold=LATENCY_WARN_MS,
                )
            return ComponentHealthResult.ok(
                self.component_name,
                latency_ms=latency_ms,
                ping="ok",
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            logger.warning("[monitor:mongodb] check failed: %s", exc)
            return ComponentHealthResult.unhealthy(
                self.component_name,
                message=f"MongoDB unreachable: {exc}",
                latency_ms=latency_ms,
            )
