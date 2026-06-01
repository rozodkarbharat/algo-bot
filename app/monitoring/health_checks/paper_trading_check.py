"""Paper trading engine health check — session state, account and position status."""

from __future__ import annotations

import time

from app.monitoring.health_checks.base import BaseHealthCheck, ComponentHealthResult
from app.utils.logger import get_logger

logger = get_logger(__name__)


class PaperTradingHealthCheck(BaseHealthCheck):
    """
    Check the paper trading engine health.

    Examines:
      - Whether the paper session account exists (session warmed up)
      - Paper account pause state and pause reason
      - Current daily P&L
      - Number of open paper positions
      - Available capital
    """

    @property
    def component_name(self) -> str:
        return "paper_trading_engine"

    async def _run(self) -> ComponentHealthResult:
        t0 = time.perf_counter()
        try:
            from app.paper_trading.session_manager import PaperSessionManager
            from app.repositories.paper_account_repository import PaperAccountRepository
            from app.repositories.paper_position_repository import PaperPositionRepository
            from app.models.paper_account import DEFAULT_PAPER_ACCOUNT_ID

            account_repo = PaperAccountRepository()
            position_repo = PaperPositionRepository()

            account = await account_repo.get_by_account_id(DEFAULT_PAPER_ACCOUNT_ID)
            latency_ms = (time.perf_counter() - t0) * 1000

            if account is None:
                return ComponentHealthResult.degraded(
                    self.component_name,
                    latency_ms=latency_ms,
                    message="Paper session not yet warmed up: account not found in DB.",
                    session_active=False,
                    open_positions=0,
                    available_capital=None,
                    daily_pnl=None,
                )

            open_positions = await position_repo.count_open()
            latency_ms = (time.perf_counter() - t0) * 1000

            meta = {
                "session_active": not account.is_paused,
                "open_positions": open_positions,
                "available_capital": account.available_capital,
                "daily_pnl": account.daily_pnl,
                "used_capital": account.used_capital,
                "unrealized_pnl": account.unrealized_pnl,
                "realized_pnl": account.realized_pnl,
                "is_paused": account.is_paused,
                "pause_reason": account.pause_reason,
                "consecutive_losses": account.consecutive_losses,
                "total_trades": account.total_trades,
            }

            if account.is_paused:
                return ComponentHealthResult.degraded(
                    self.component_name,
                    latency_ms=latency_ms,
                    message=f"Paper trading paused: {account.pause_reason or 'manual'}",
                    **meta,
                )

            return ComponentHealthResult.ok(self.component_name, latency_ms=latency_ms, **meta)

        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            logger.warning("[monitor:paper-trading] check failed: %s", exc)
            return ComponentHealthResult.unhealthy(
                self.component_name,
                message=f"Paper trading check failed: {exc}",
                latency_ms=latency_ms,
            )
