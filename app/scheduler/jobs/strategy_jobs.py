"""
APScheduler jobs for the strategy engine.

Jobs defined here:
  daily_osd_detection         — 4:00 PM IST (after EOD candle sync at 3:45 PM)
  daily_probability_update    — 4:15 PM IST (after OSD detection)
  daily_shortlist_generation  — 4:30 PM IST (after probability update)

Schedule rationale:
  3:45 PM — EOD candle sync fetches today's 15-min candles (market_data_jobs)
  4:00 PM — OSD detection classifies today as one-side/choppy/invalid
  4:15 PM — Probability engine recalculates P(OSD_today | OSD_yesterday) per stock
  4:30 PM — Shortlist engine identifies tomorrow's tradable candidates

This pipeline runs Monday–Friday so the shortlist is ready before next morning.

Registration:
  register_strategy_jobs() is called from scheduler/scheduler.py at startup.
"""

from app.utils.logger import get_logger
from app.utils.trading_day import last_completed_trading_day

logger = get_logger(__name__)


# ── Job 1: Daily One-Side Detection ──────────────────────────────────────────

async def daily_osd_detection() -> None:
    """
    Classify today's candles as one-side / choppy / invalid for all NIFTY50 stocks.

    Triggered: daily at 16:00 IST (Monday–Friday).
    Depends on: EOD candle sync completing at 15:45.
    """
    logger.info("=== Daily OSD Detection job started ===")
    try:
        from app.services.strategy_service import StrategyService

        svc = StrategyService()
        trading_date = last_completed_trading_day()

        result = await svc.run_detection_for_date(trading_date=trading_date)
        logger.info(
            "=== OSD Detection complete for %s: %d one-side / %d choppy / %d invalid"
            " | %d written | %.1fs ===",
            trading_date,
            result.one_side_days,
            result.choppy_days,
            result.invalid_days,
            result.records_written,
            result.duration_seconds,
        )
        if result.failed_symbols:
            logger.warning("OSD Detection — failed symbols: %s", result.failed_symbols)

    except Exception as exc:
        logger.error(
            "Daily OSD detection job failed with unhandled error: %s", exc, exc_info=True
        )


# ── Job 2: Daily Continuation Statistics Calculation ─────────────────────────

async def daily_probability_update() -> None:
    """
    Recalculate P(OneSideToday | OneSideYesterday) for all NIFTY50 stocks.

    Triggered: daily at 16:15 IST (Monday–Friday).
    Depends on: daily_osd_detection completing at 16:00.
    """
    logger.info("=== Daily Probability Update job started ===")
    try:
        from app.services.strategy_service import StrategyService

        svc = StrategyService()
        result = await svc.calculate_all_continuation_stats()

        logger.info(
            "=== Probability Update complete: %d tradable / %d total | %.1fs ===",
            result.tradable_symbols,
            result.total_symbols,
            result.duration_seconds,
        )
        if result.failed_symbols:
            logger.warning("Probability Update — failed symbols: %s", result.failed_symbols)

    except Exception as exc:
        logger.error(
            "Daily probability update job failed with unhandled error: %s", exc, exc_info=True
        )


# ── Job 3: Daily Shortlist Generation ────────────────────────────────────────

async def daily_shortlist_generation() -> None:
    """
    Generate tomorrow's tradable shortlist from today's one-side stocks
    and their continuation probabilities.

    Triggered: daily at 16:30 IST (Monday–Friday).
    Depends on: daily_probability_update completing at 16:15.

    The shortlist is available via GET /api/v1/shortlist/today from 4:30 PM onward.
    """
    logger.info("=== Daily Shortlist Generation job started ===")
    try:
        from app.core.exceptions import ConflictException
        from app.services.shortlist_service import shortlist_run_manager
        from app.utils.trading_day import get_next_trading_day

        # Generate for the NEXT trading day (today's shortlist = what we trade tomorrow).
        # Routed through the run-manager so manual /api/v1/shortlist/run and the
        # scheduler share the same single-flight lock and status state.
        next_trading_day = get_next_trading_day(last_completed_trading_day())
        try:
            result = await shortlist_run_manager.run(
                target_date=next_trading_day, trigger="scheduler"
            )
        except ConflictException:
            logger.warning(
                "Skipping scheduled shortlist run — another run is already in progress."
            )
            return

        logger.info(
            "=== Shortlist for %s: %d candidates from %d one-side stocks | %.3fs ===",
            next_trading_day,
            len(result.entries),
            result.total_candidates_checked,
            result.duration_seconds,
        )

        if result.entries:
            symbols = [
                f"{e.symbol}({e.direction},{e.continuation_probability:.0%})"
                for e in result.entries
            ]
            logger.info("Shortlist candidates: %s", ", ".join(symbols))
        else:
            logger.info("No tradable candidates found for %s.", next_trading_day)

    except Exception as exc:
        logger.error(
            "Daily shortlist generation job failed with unhandled error: %s", exc, exc_info=True
        )


# ── Registration helper ───────────────────────────────────────────────────────

def register_strategy_jobs(scheduler) -> None:  # type: ignore[type-arg]
    """
    Register all strategy pipeline jobs with the provided APScheduler instance.

    Called once at application startup from scheduler/scheduler.py.
    All times are IST (Asia/Kolkata) — the scheduler timezone.
    """
    # Job 1: OSD Detection — 4:00 PM IST, Monday to Friday
    scheduler.add_job(
        daily_osd_detection,
        trigger="cron",
        day_of_week="mon-fri",
        hour=16,
        minute=0,
        id="daily_osd_detection",
        name="Daily One-Side Day Detection",
        replace_existing=True,
    )
    logger.info("Registered job: daily_osd_detection (Mon–Fri 16:00 IST)")

    # Job 2: Probability Update — 4:15 PM IST, Monday to Friday
    scheduler.add_job(
        daily_probability_update,
        trigger="cron",
        day_of_week="mon-fri",
        hour=16,
        minute=15,
        id="daily_probability_update",
        name="Daily Continuation Probability Update",
        replace_existing=True,
    )
    logger.info("Registered job: daily_probability_update (Mon–Fri 16:15 IST)")

    # Job 3: Shortlist Generation — 4:30 PM IST, Monday to Friday
    scheduler.add_job(
        daily_shortlist_generation,
        trigger="cron",
        day_of_week="mon-fri",
        hour=16,
        minute=30,
        id="daily_shortlist_generation",
        name="Daily Shortlist Generation",
        replace_existing=True,
    )
    logger.info("Registered job: daily_shortlist_generation (Mon–Fri 16:30 IST)")
