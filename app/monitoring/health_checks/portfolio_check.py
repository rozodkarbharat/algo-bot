"""Portfolio engine health check — risk state and halt status."""

from __future__ import annotations

import time

from app.monitoring.health_checks.base import BaseHealthCheck, ComponentHealthResult
from app.utils.logger import get_logger
from app.utils.trading_day import today_ist
from app.utils.market_time import date_to_utc_midnight

logger = get_logger(__name__)


class PortfolioHealthCheck(BaseHealthCheck):
    """
    Check the portfolio risk state for today.

    Marks degraded when the portfolio is halted (daily loss limit hit)
    or when exposure exceeds the configured warning threshold.
    """

    @property
    def component_name(self) -> str:
        return "portfolio_engine"

    async def _run(self) -> ComponentHealthResult:
        t0 = time.perf_counter()
        try:
            from app.repositories.portfolio_risk_state_repository import (
                PortfolioRiskStateRepository,
            )
            from app.config.settings import settings

            repo = PortfolioRiskStateRepository()
            today_dt = date_to_utc_midnight(today_ist())
            state = await repo.get_for_date(today_dt)
            latency_ms = (time.perf_counter() - t0) * 1000

            if state is None:
                return ComponentHealthResult.ok(
                    self.component_name,
                    latency_ms=latency_ms,
                    note="No portfolio state for today yet (session may not have started).",
                    total_capital=settings.PORTFOLIO_TOTAL_CAPITAL,
                )

            utilization = (
                round(state.used_capital / state.total_capital * 100, 2)
                if state.total_capital
                else 0.0
            )
            meta = {
                "total_capital": state.total_capital,
                "used_capital": state.used_capital,
                "available_capital": state.available_capital,
                "open_positions": state.open_positions,
                "utilization_pct": utilization,
                "is_halted": state.is_halted,
                "halt_reason": state.halt_reason,
                "approved_today": state.total_approved_today,
                "rejected_today": state.total_rejected_today,
            }

            if state.is_halted:
                return ComponentHealthResult.degraded(
                    self.component_name,
                    latency_ms=latency_ms,
                    message=f"Portfolio halted: {state.halt_reason or 'unknown reason'}",
                    **meta,
                )

            warn_threshold = settings.PORTFOLIO_MAX_CAPITAL_EXPOSURE_PCT * 0.9
            if utilization >= warn_threshold:
                return ComponentHealthResult.degraded(
                    self.component_name,
                    latency_ms=latency_ms,
                    message=f"Portfolio utilization high: {utilization:.1f}%",
                    **meta,
                )

            return ComponentHealthResult.ok(self.component_name, latency_ms=latency_ms, **meta)

        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            logger.warning("[monitor:portfolio] check failed: %s", exc)
            return ComponentHealthResult.unhealthy(
                self.component_name,
                message=f"Portfolio check failed: {exc}",
                latency_ms=latency_ms,
            )
