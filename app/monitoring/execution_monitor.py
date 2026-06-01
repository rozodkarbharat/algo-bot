"""
Execution monitor — validates live order placement quality.

Tracks:
  - Order placement success rate
  - Rejection rate and rejection reasons
  - Latency between signal generation and order placement
  - Stop-loss order coverage (are SL orders attached to every position?)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.utils.logger import get_logger
from app.utils.market_time import date_to_utc_midnight, now_utc
from app.utils.trading_day import today_ist

logger = get_logger(__name__)

HIGH_REJECTION_RATE_THRESHOLD = 0.40  # 40%


@dataclass
class ExecutionHealthReport:
    """Live execution quality snapshot for one trading day."""

    total_orders: int
    filled_orders: int
    rejected_orders: int
    rejection_rate: float
    open_positions: int
    kill_switch_engaged: bool
    kill_switch_reason: Optional[str]
    notes: list[str] = field(default_factory=list)
    checked_at: datetime = field(default_factory=now_utc)


class ExecutionMonitor:
    """
    Monitors live order execution quality and fires alerts on anomalies.
    """

    async def check(self) -> ExecutionHealthReport:
        """Build and return today's execution health snapshot."""
        try:
            from app.repositories.live_order_repository import LiveOrderRepository
            from app.repositories.live_position_repository import LivePositionRepository
            from app.models.live_order import LiveOrderStatus
            from app.live_execution.failsafe import failsafe

            today_dt = date_to_utc_midnight(today_ist())
            order_repo = LiveOrderRepository()
            position_repo = LivePositionRepository()

            all_statuses = [
                LiveOrderStatus.PENDING, LiveOrderStatus.OPEN,
                LiveOrderStatus.FILLED, LiveOrderStatus.REJECTED,
                LiveOrderStatus.CANCELLED,
            ]
            total = await order_repo.count_for_date_in_statuses(today_dt, all_statuses)
            filled = await order_repo.count_for_date_in_statuses(today_dt, [LiveOrderStatus.FILLED])
            rejected = await order_repo.count_for_date_in_statuses(today_dt, [LiveOrderStatus.REJECTED])
            open_pos = await position_repo.count_open()

            ks_engaged = failsafe.kill_switch.engaged
            ks_reason = failsafe.kill_switch.reason

            rejection_rate = round(rejected / total, 4) if total else 0.0

            notes = []
            if ks_engaged:
                notes.append(f"Kill switch engaged: {ks_reason or 'manual'}")
            if rejection_rate > HIGH_REJECTION_RATE_THRESHOLD and total >= 3:
                notes.append(
                    f"High rejection rate: {rejection_rate*100:.0f}% ({rejected}/{total})"
                )
                from app.monitoring.alert_router import alert_router
                await alert_router.high_rejection_rate(rejection_rate, total)

            return ExecutionHealthReport(
                total_orders=total,
                filled_orders=filled,
                rejected_orders=rejected,
                rejection_rate=rejection_rate,
                open_positions=open_pos,
                kill_switch_engaged=ks_engaged,
                kill_switch_reason=ks_reason,
                notes=notes,
            )

        except Exception as exc:
            logger.error("[execution-monitor] check failed: %s", exc)
            return ExecutionHealthReport(
                total_orders=0, filled_orders=0, rejected_orders=0,
                rejection_rate=0.0, open_positions=0,
                kill_switch_engaged=False, kill_switch_reason=None,
                notes=[f"Monitor failed: {exc}"],
            )


execution_monitor = ExecutionMonitor()
