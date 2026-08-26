"""
APScheduler jobs for the strategy engine.

Jobs defined here:
  daily_orhv_shortlist — 4:00 PM IST (after EOD candle sync at 3:45 PM)

Schedule rationale:
  3:45 PM — EOD candle sync fetches today's 15-min candles (market_data_jobs)
  4:00 PM — ORHV Phase 1 + 2 builds tomorrow's tradable shortlist

This pipeline runs Monday–Friday so the shortlist is ready before next morning.

Registration:
  register_strategy_jobs() is called from scheduler/scheduler.py at startup.
"""

from app.utils.logger import get_logger
from app.utils.trading_day import last_completed_trading_day

logger = get_logger(__name__)


async def daily_orhv_shortlist() -> None:
    """
    Run ORHV Phase 1 + 2 for today's session and build tomorrow's tradable list.

    Triggered: daily at 16:00 IST (Monday–Friday), after EOD candle sync.
    Uses ORHVRunManager for single-flight consistency with manual POST /orhv/run.
    """
    logger.info("=== Daily ORHV Shortlist job started ===")
    try:
        from app.core.exceptions import ConflictException
        from app.services.orhv_service import orhv_run_manager
        from app.utils.trading_day import get_next_trading_day

        execution_date = get_next_trading_day(last_completed_trading_day())
        try:
            result = await orhv_run_manager.run(
                target_date=execution_date,
                trigger="scheduler",
                full_pipeline=True,
            )
        except ConflictException:
            logger.warning(
                "Skipping scheduled ORHV run — another ORHV run is already in progress."
            )
            return

        tradable = sum(1 for e in result.entries if e.tradable)
        logger.info(
            "=== ORHV shortlist for %s: %d tradable / %d candidates | %.3fs ===",
            execution_date,
            tradable,
            len(result.entries),
            result.duration_seconds,
        )
    except Exception as exc:
        logger.error(
            "Daily ORHV shortlist job failed with unhandled error: %s", exc, exc_info=True
        )


def register_strategy_jobs(scheduler) -> None:  # type: ignore[type-arg]
    """
    Register all strategy pipeline jobs with the provided APScheduler instance.

    Called once at application startup from scheduler/scheduler.py.
    All times are IST (Asia/Kolkata) — the scheduler timezone.
    """
    scheduler.add_job(
        daily_orhv_shortlist,
        trigger="cron",
        day_of_week="mon-fri",
        hour=16,
        minute=0,
        id="daily_orhv_shortlist",
        name="Daily ORHV Shortlist",
        replace_existing=True,
    )
    logger.info("Registered job: daily_orhv_shortlist (Mon–Fri 16:00 IST)")
