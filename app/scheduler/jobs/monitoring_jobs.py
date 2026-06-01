"""
Monitoring scheduler jobs.

Runs the health aggregator every 60 seconds during market hours (and every
5 minutes outside market hours) to keep SystemHealthStatus up to date and
detect stale components early.
"""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.utils.logger import get_logger

logger = get_logger(__name__)


async def run_health_checks() -> None:
    """Run all component health checks and persist results."""
    try:
        from app.monitoring.health_aggregator import health_aggregator
        report = await health_aggregator.run_all()
        logger.info(
            "[monitoring-job] health check complete: overall=%s healthy=%d degraded=%d unhealthy=%d",
            report.overall_status,
            report.healthy_count,
            report.degraded_count,
            report.unhealthy_count,
        )
    except Exception as exc:
        logger.error("[monitoring-job] health check failed: %s", exc, exc_info=True)


async def daily_ops_report() -> None:
    """Generate and send the daily operations report at EOD."""
    try:
        from app.monitoring.daily_report import daily_report_generator
        from app.services.notification_service import notification_service
        report = await daily_report_generator.generate()
        logger.info(
            "[monitoring-job] daily report: signals=%d paper_trades=%d paper_pnl=%.2f incidents=%d",
            report.signals_generated,
            report.paper_trades,
            report.paper_pnl,
            report.open_incidents,
        )
    except Exception as exc:
        logger.error("[monitoring-job] daily report failed: %s", exc, exc_info=True)


def register_monitoring_jobs(scheduler: AsyncIOScheduler) -> None:
    """Register monitoring scheduler jobs."""

    # Health checks every 60 seconds on weekdays
    scheduler.add_job(
        run_health_checks,
        trigger="cron",
        day_of_week="mon-fri",
        hour="9-16",
        minute="*",
        second="0",
        id="health_check_market_hours",
        replace_existing=True,
    )

    # Health checks every 5 minutes outside market hours (keep DB warm)
    scheduler.add_job(
        run_health_checks,
        trigger="cron",
        day_of_week="mon-fri",
        hour="0-8,17-23",
        minute="*/5",
        id="health_check_off_hours",
        replace_existing=True,
    )

    # Daily ops report at 15:50 IST (after EOD close)
    scheduler.add_job(
        daily_ops_report,
        trigger="cron",
        day_of_week="mon-fri",
        hour=15,
        minute=50,
        id="daily_ops_report",
        replace_existing=True,
    )

    logger.info("[monitoring-jobs] registered: health_check (market hours + off hours), daily_ops_report")
