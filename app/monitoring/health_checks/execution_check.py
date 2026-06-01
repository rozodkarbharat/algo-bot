"""Live execution engine health check — order rejection rate and kill switch."""

from __future__ import annotations

import time

from app.monitoring.health_checks.base import BaseHealthCheck, ComponentHealthResult
from app.utils.logger import get_logger

logger = get_logger(__name__)

MAX_REJECTION_RATE = 0.5   # >50% rejection rate → degraded


class ExecutionHealthCheck(BaseHealthCheck):
    """
    Check the live execution engine health.

    Examines:
      - Kill switch state
      - Today's order rejection rate
      - Open live position count
    """

    @property
    def component_name(self) -> str:
        return "execution_engine"

    async def _run(self) -> ComponentHealthResult:
        t0 = time.perf_counter()
        try:
            from app.live_execution.failsafe import failsafe
            from app.repositories.live_order_repository import LiveOrderRepository
            from app.repositories.live_position_repository import LivePositionRepository
            from app.models.live_order import LiveOrderStatus
            from app.utils.market_time import date_to_utc_midnight
            from app.utils.trading_day import today_ist

            kill_switch_engaged = failsafe.kill_switch.engaged
            kill_reason = failsafe.kill_switch.reason

            today_dt = date_to_utc_midnight(today_ist())
            order_repo = LiveOrderRepository()
            position_repo = LivePositionRepository()

            total_orders = await order_repo.count_for_date_in_statuses(
                today_dt,
                [
                    LiveOrderStatus.PENDING,
                    LiveOrderStatus.OPEN,
                    LiveOrderStatus.FILLED,
                    LiveOrderStatus.REJECTED,
                    LiveOrderStatus.CANCELLED,
                ],
            )
            rejected_orders = await order_repo.count_for_date_in_statuses(
                today_dt, [LiveOrderStatus.REJECTED]
            )
            open_positions = await position_repo.count_open()

            latency_ms = (time.perf_counter() - t0) * 1000

            rejection_rate = (
                round(rejected_orders / total_orders, 4) if total_orders else 0.0
            )

            meta = {
                "kill_switch_engaged": kill_switch_engaged,
                "kill_switch_reason": kill_reason,
                "total_orders_today": total_orders,
                "rejected_orders_today": rejected_orders,
                "rejection_rate": rejection_rate,
                "open_live_positions": open_positions,
            }

            if kill_switch_engaged:
                return ComponentHealthResult.degraded(
                    self.component_name,
                    latency_ms=latency_ms,
                    message=f"Kill switch engaged: {kill_reason or 'manual'}",
                    **meta,
                )

            if rejection_rate > MAX_REJECTION_RATE and total_orders >= 3:
                return ComponentHealthResult.degraded(
                    self.component_name,
                    latency_ms=latency_ms,
                    message=f"High order rejection rate: {rejection_rate*100:.0f}%",
                    **meta,
                )

            return ComponentHealthResult.ok(self.component_name, latency_ms=latency_ms, **meta)

        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            logger.warning("[monitor:execution] check failed: %s", exc)
            return ComponentHealthResult.unhealthy(
                self.component_name,
                message=f"Execution check failed: {exc}",
                latency_ms=latency_ms,
            )
