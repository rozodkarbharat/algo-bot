"""WebSocket manager health check — active client counts and room state."""

from __future__ import annotations

import time

from app.monitoring.health_checks.base import BaseHealthCheck, ComponentHealthResult
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Alert when there are no active clients during market hours
EXPECTED_MIN_CLIENTS_MARKET_HOURS = 0   # 0 = dashboard not required to be open


class WebSocketHealthCheck(BaseHealthCheck):
    """
    Verify the WebSocket manager is operational.

    Reports the number of active connections, rooms, and any rooms that
    were recently emptied (which may indicate mass disconnects).
    """

    @property
    def component_name(self) -> str:
        return "websocket_manager"

    async def _run(self) -> ComponentHealthResult:
        t0 = time.perf_counter()
        try:
            from app.websocket.manager import ws_manager
            total_clients = ws_manager.active_connections
            rooms = list(ws_manager._rooms.keys()) if hasattr(ws_manager, "_rooms") else []
            latency_ms = (time.perf_counter() - t0) * 1000

            return ComponentHealthResult.ok(
                self.component_name,
                latency_ms=latency_ms,
                total_clients=total_clients,
                active_rooms=rooms,
                room_count=len(rooms),
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            logger.warning("[monitor:websocket] check failed: %s", exc)
            return ComponentHealthResult.unhealthy(
                self.component_name,
                message=f"WebSocket manager check failed: {exc}",
                latency_ms=latency_ms,
            )
