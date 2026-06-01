"""Broker API health check — verifies the Angel One session is valid."""

from __future__ import annotations

import time
from typing import Optional

from app.monitoring.health_checks.base import BaseHealthCheck, ComponentHealthResult
from app.utils.logger import get_logger

logger = get_logger(__name__)


class BrokerHealthCheck(BaseHealthCheck):
    """
    Verify the broker session is active and the API is reachable.

    Uses the `angel_one_auth` singleton (lazy import to avoid circular deps
    when the check is constructed before the broker module is imported).
    """

    @property
    def component_name(self) -> str:
        return "broker_angelone"

    async def _run(self) -> ComponentHealthResult:
        t0 = time.perf_counter()
        try:
            from app.brokers.angelone.auth import angel_one_auth
            session = await angel_one_auth.get_session()
            latency_ms = (time.perf_counter() - t0) * 1000

            if session is None:
                return ComponentHealthResult.unhealthy(
                    self.component_name,
                    message="Broker session is None — authentication may have failed.",
                    latency_ms=latency_ms,
                )

            # Check session expiry
            from app.utils.market_time import now_utc
            from datetime import timedelta
            now = now_utc()
            expires_in = None
            if hasattr(session, "expires_at") and session.expires_at:
                expires_in = (session.expires_at - now).total_seconds()
                if expires_in < 300:  # less than 5 minutes left
                    return ComponentHealthResult.degraded(
                        self.component_name,
                        latency_ms=latency_ms,
                        message=f"Broker session expiring soon: {expires_in:.0f}s remaining",
                        expires_in_seconds=round(expires_in),
                    )

            return ComponentHealthResult.ok(
                self.component_name,
                latency_ms=latency_ms,
                authenticated=True,
                expires_in_seconds=round(expires_in) if expires_in else None,
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            logger.warning("[monitor:broker] check failed: %s", exc)
            return ComponentHealthResult.unhealthy(
                self.component_name,
                message=f"Broker check failed: {exc}",
                latency_ms=latency_ms,
            )
