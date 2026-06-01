"""
Notification scheduler jobs.

Jobs registered here:
  - daily_summary_job : Mon–Fri 15:45 IST — sends the day's P&L summary
    via all enabled notification providers.

Register in app/scheduler/scheduler.py's _register_all_jobs().
"""

from app.utils.logger import get_logger

logger = get_logger(__name__)


async def send_daily_summary_job() -> None:
    """
    Scheduled job: build and dispatch the daily summary.

    Fires at 15:45 IST — 30 minutes after the EOD force-exit (15:15),
    giving enough time for all positions to close before the summary runs.
    """
    logger.info("Daily summary job started")
    try:
        from app.services.notification_service import notification_service
        await notification_service.send_daily_summary(mode="Paper")
        logger.info("Daily summary job completed")
    except Exception as exc:
        logger.error("Daily summary job failed: %s", exc, exc_info=True)
        # Notify about the failure itself (system alert, no infinite loop risk
        # because the dedup key is different from the summary dedup key).
        try:
            from app.services.notification_service import notification_service
            await notification_service.on_scheduler_failure(
                job_id="daily_summary_job",
                error=str(exc),
            )
        except Exception:
            pass


def register_notification_jobs(scheduler) -> None:
    """
    Register all notification-related scheduled jobs.

    Called from _register_all_jobs() in app/scheduler/scheduler.py.

    Args:
        scheduler: The running AsyncIOScheduler instance.
    """
    # Daily P&L summary at 15:45 IST, Monday–Friday.
    scheduler.add_job(
        send_daily_summary_job,
        trigger="cron",
        id="daily_summary_job",
        day_of_week="mon-fri",
        hour=15,
        minute=45,
        timezone="Asia/Kolkata",
        replace_existing=True,
    )
    logger.info("Registered notification job: daily_summary_job (Mon–Fri 15:45 IST)")
