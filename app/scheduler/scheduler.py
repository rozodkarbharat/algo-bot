"""
APScheduler initialisation and lifecycle management.

Uses AsyncIOScheduler so all jobs run inside the same asyncio event loop
as FastAPI — no thread synchronisation needed for async tasks.

Timezone defaults to Asia/Kolkata (IST) to align with NSE/BSE market hours.

Job categories (to be wired later):
  - Market data ingestion      (e.g. every 1 min during market hours)
  - Strategy signal generation (e.g. every 5 min)
  - EOD reconciliation         (e.g. 15:35 IST daily)
  - Health/cleanup jobs        (e.g. nightly log rotation)

Usage:
    from app.scheduler.scheduler import scheduler
    scheduler.add_job(my_async_fn, "cron", hour=9, minute=15)
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.executors.asyncio import AsyncIOExecutor

from app.config.settings import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

# ── Scheduler configuration ───────────────────────────────────────────────────

_jobstores = {
    # "default" store keeps jobs in memory.
    # Swap with MongoDBJobStore for persistence across restarts:
    #   from apscheduler.jobstores.mongodb import MongoDBJobStore
    #   "default": MongoDBJobStore(database="trading_bot", collection="scheduler_jobs")
    "default": MemoryJobStore(),
}

_executors = {
    # AsyncIOExecutor runs coroutine jobs in the existing event loop.
    "default": AsyncIOExecutor(),
}

_job_defaults = {
    "coalesce": True,        # collapse missed runs into one execution
    "max_instances": 1,      # prevent overlapping runs of the same job
    "misfire_grace_time": 30,  # seconds before a missed job is discarded
}

scheduler = AsyncIOScheduler(
    jobstores=_jobstores,
    executors=_executors,
    job_defaults=_job_defaults,
    timezone=settings.SCHEDULER_TIMEZONE,
)


# ── Lifecycle helpers ─────────────────────────────────────────────────────────

def start_scheduler() -> None:
    """Start the scheduler and register all jobs. Called from FastAPI startup event."""
    if not scheduler.running:
        # Register jobs BEFORE starting so they are available immediately.
        _register_all_jobs()
        scheduler.start()
        logger.info(
            "APScheduler started. Timezone: %s | Jobs: %d",
            settings.SCHEDULER_TIMEZONE,
            len(scheduler.get_jobs()),
        )
    else:
        logger.warning("APScheduler start() called but it is already running.")


def stop_scheduler() -> None:
    """Gracefully shut down the scheduler. Called from FastAPI shutdown event."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("APScheduler stopped.")


def get_scheduler() -> AsyncIOScheduler:
    """Return the module-level scheduler instance (for system health checks)."""
    return scheduler


# ── Job registration ──────────────────────────────────────────────────────────

def _register_all_jobs() -> None:
    """
    Register all scheduled jobs.

    Add new job modules here as features are built out.
    Jobs are registered before the scheduler starts so they are
    immediately visible and the first fire is correctly scheduled.
    """
    from app.scheduler.jobs.market_data_jobs import register_market_data_jobs
    register_market_data_jobs(scheduler)

    from app.scheduler.jobs.strategy_jobs import register_strategy_jobs
    register_strategy_jobs(scheduler)

    from app.scheduler.jobs.live_engine_jobs import register_live_engine_jobs
    register_live_engine_jobs(scheduler)

    from app.scheduler.jobs.paper_trading_jobs import register_paper_trading_jobs
    register_paper_trading_jobs(scheduler)

    from app.scheduler.jobs.live_execution_jobs import register_live_execution_jobs
    register_live_execution_jobs(scheduler)

    from app.scheduler.jobs.notification_jobs import register_notification_jobs
    register_notification_jobs(scheduler)

    from app.scheduler.jobs.monitoring_jobs import register_monitoring_jobs
    register_monitoring_jobs(scheduler)

    from app.scheduler.jobs.reconciliation_jobs import register_reconciliation_jobs
    register_reconciliation_jobs(scheduler)
