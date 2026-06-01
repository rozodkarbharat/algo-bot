"""
Daily operation report generator.

Collects and formats the end-of-day operations summary:
  - Broker uptime / reconnect count
  - Signals generated today
  - Trades executed (paper + live)
  - P&L summary
  - Open incidents and errors
  - Scheduled job execution summary

Called by the EOD notification scheduler job at 15:45 IST.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from app.utils.logger import get_logger
from app.utils.market_time import date_to_utc_midnight, now_utc
from app.utils.trading_day import today_ist

logger = get_logger(__name__)


@dataclass
class DailyOperationReport:
    """Complete EOD operations summary."""

    trading_date: date
    generated_at: datetime = field(default_factory=now_utc)

    # ── Broker ────────────────────────────────────────────────────────────────
    broker_reconnects: int = 0
    broker_session_healthy: bool = True

    # ── Signals ───────────────────────────────────────────────────────────────
    signals_generated: int = 0
    signals_approved: int = 0
    signals_rejected: int = 0

    # ── Trades ────────────────────────────────────────────────────────────────
    paper_trades: int = 0
    paper_pnl: float = 0.0
    live_trades: int = 0
    live_pnl: float = 0.0

    # ── Risk ──────────────────────────────────────────────────────────────────
    portfolio_halted: bool = False
    halt_reason: Optional[str] = None
    max_exposure_reached_pct: float = 0.0

    # ── Incidents ─────────────────────────────────────────────────────────────
    open_incidents: int = 0
    resolved_incidents: int = 0
    critical_incidents: int = 0

    # ── Errors ────────────────────────────────────────────────────────────────
    total_alerts_fired: int = 0
    undelivered_alerts: int = 0

    notes: list[str] = field(default_factory=list)


class DailyReportGenerator:
    """Compiles the daily operations report from all data sources."""

    async def generate(self, trading_date: Optional[date] = None) -> DailyOperationReport:
        """Build the daily ops report for the given trading date (defaults to today)."""
        report_date = trading_date or today_ist()
        report = DailyOperationReport(trading_date=report_date)
        today_dt = date_to_utc_midnight(report_date)

        await self._collect_signal_stats(report, today_dt)
        await self._collect_trade_stats(report, today_dt)
        await self._collect_risk_stats(report, today_dt)
        await self._collect_incident_stats(report)
        await self._collect_alert_stats(report, today_dt)
        await self._collect_broker_stats(report)

        return report

    # ── Data collectors ───────────────────────────────────────────────────────

    async def _collect_signal_stats(self, report: DailyOperationReport, today_dt: datetime) -> None:
        try:
            from app.repositories.live_signal_repository import LiveSignalRepository
            from app.repositories.portfolio_allocation_repository import PortfolioAllocationRepository
            from app.models.portfolio_allocation import AllocationStatus

            sig_repo = LiveSignalRepository()
            alloc_repo = PortfolioAllocationRepository()

            signals = await sig_repo.get_signals_for_date(today_dt)
            report.signals_generated = len(signals)

            approved = await alloc_repo.count_approved_for_date(today_dt)
            all_allocs = await alloc_repo.get_for_date(today_dt)
            report.signals_approved = approved
            report.signals_rejected = len(all_allocs) - approved
        except Exception as exc:
            logger.warning("[daily-report] signal stats failed: %s", exc)

    async def _collect_trade_stats(self, report: DailyOperationReport, today_dt: datetime) -> None:
        try:
            from app.repositories.paper_trade_repository import PaperTradeRepository
            from app.repositories.live_position_repository import LivePositionRepository

            paper_repo = PaperTradeRepository()
            live_repo = LivePositionRepository()

            paper_trades = await paper_repo.get_for_date(today_dt)
            report.paper_trades = len(paper_trades)
            report.paper_pnl = round(sum(t.pnl or 0.0 for t in paper_trades), 2)

            live_positions = await live_repo.get_for_date(today_dt)
            closed = [p for p in live_positions if p.status.value == "closed"]
            report.live_trades = len(closed)
            report.live_pnl = round(sum(p.realized_pnl or 0.0 for p in closed), 2)
        except Exception as exc:
            logger.warning("[daily-report] trade stats failed: %s", exc)

    async def _collect_risk_stats(self, report: DailyOperationReport, today_dt: datetime) -> None:
        try:
            from app.repositories.portfolio_risk_state_repository import PortfolioRiskStateRepository
            from app.config.settings import settings

            repo = PortfolioRiskStateRepository()
            state = await repo.get_for_date(today_dt)
            if state:
                report.portfolio_halted = state.is_halted
                report.halt_reason = state.halt_reason
                if state.total_capital > 0:
                    report.max_exposure_reached_pct = round(
                        state.used_capital / state.total_capital * 100, 2
                    )
        except Exception as exc:
            logger.warning("[daily-report] risk stats failed: %s", exc)

    async def _collect_incident_stats(self, report: DailyOperationReport) -> None:
        try:
            from app.monitoring.incident_manager import incident_manager
            from app.models.system_incident import IncidentStatus
            from app.models.alert_event import AlertSeverity

            all_incidents = await incident_manager.list_recent(limit=200)
            today = today_ist()
            today_incidents = [
                i for i in all_incidents
                if i.detected_at.date() == today
            ]
            report.open_incidents = sum(
                1 for i in today_incidents if i.status != IncidentStatus.RESOLVED
            )
            report.resolved_incidents = sum(
                1 for i in today_incidents if i.status == IncidentStatus.RESOLVED
            )
            report.critical_incidents = sum(
                1 for i in today_incidents if i.severity == AlertSeverity.CRITICAL
            )
        except Exception as exc:
            logger.warning("[daily-report] incident stats failed: %s", exc)

    async def _collect_alert_stats(self, report: DailyOperationReport, today_dt: datetime) -> None:
        try:
            from app.repositories.alert_event_repository import AlertEventRepository
            alerts = await AlertEventRepository().get_recent(limit=500)
            today_alerts = [
                a for a in alerts
                if a.timestamp.date() == today_ist()
            ]
            report.total_alerts_fired = len(today_alerts)
            report.undelivered_alerts = sum(1 for a in today_alerts if not a.delivered)
        except Exception as exc:
            logger.warning("[daily-report] alert stats failed: %s", exc)

    async def _collect_broker_stats(self, report: DailyOperationReport) -> None:
        try:
            from app.live.market_engine import live_market_engine
            stats = live_market_engine.stats
            report.broker_reconnects = stats.reconnect_count
        except Exception as exc:
            logger.warning("[daily-report] broker stats failed: %s", exc)


daily_report_generator = DailyReportGenerator()
