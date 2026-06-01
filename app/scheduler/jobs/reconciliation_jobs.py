"""
Scheduler jobs for broker reconciliation.

Jobs registered here:
  broker_reconciliation      — every 5 min during market hours (09:15–15:30 IST)
  broker_reconciliation_eod  — once at 15:20 IST (post-EOD-close final check)

Schedule rationale:
  - 5-minute interval strikes the balance between catching discrepancies quickly
    and not hammering the broker API. The high-frequency live_order_reconcile
    job (every 30 s) handles order status polling separately.
  - The EOD run at 15:20 IST fires after live_eod_close_all (15:15) and
    verifies that all positions were successfully closed.
  - Both jobs gate on is_market_open() / market day to avoid off-hours noise.

Registration is invoked from scheduler/scheduler.py::_register_all_jobs().
"""

from app.utils.logger import get_logger

logger = get_logger(__name__)


# ── Job 1: Periodic reconciliation (every 5 min during market hours) ─────────

async def broker_reconciliation_job() -> None:
    """
    Full broker reconciliation: orders + positions + stop-losses.
    Runs every 5 minutes during market hours.
    """
    try:
        from app.utils.market_time import is_market_open
        if not is_market_open():
            return

        from app.reconciliation.broker_reconciliation_service import (
            broker_reconciliation_service,
        )
        run = await broker_reconciliation_service.run_full_reconciliation(
            broker=None,
            broker_name="AngelOne",
            trigger="scheduled_5min",
        )
        if run.discrepancies_found > 0:
            logger.warning(
                "Broker reconciliation found %d discrepancy(ies). Run ID: %s",
                run.discrepancies_found, run.run_id,
            )
        else:
            logger.debug("Broker reconciliation clean. Run ID: %s", run.run_id)
    except Exception as exc:
        logger.error("broker_reconciliation_job failed: %s", exc, exc_info=True)


# ── Job 2: Post-EOD reconciliation (15:20 IST) ────────────────────────────────

async def broker_reconciliation_eod_job() -> None:
    """
    EOD reconciliation run immediately after live_eod_close_all (15:15 IST).
    Verifies all positions are flat and no orphan positions remain at broker.
    """
    logger.info("=== EOD broker reconciliation ===")
    try:
        from app.reconciliation.broker_reconciliation_service import (
            broker_reconciliation_service,
        )
        run = await broker_reconciliation_service.run_full_reconciliation(
            broker=None,
            broker_name="AngelOne",
            trigger="eod_check",
        )
        logger.info(
            "EOD broker reconciliation completed. "
            "discrepancies=%d orders_checked=%d positions_checked=%d",
            run.discrepancies_found, run.orders_checked, run.positions_checked,
        )
    except Exception as exc:
        logger.error("broker_reconciliation_eod_job failed: %s", exc, exc_info=True)


# ── Registration helper ───────────────────────────────────────────────────────

def register_reconciliation_jobs(scheduler) -> None:  # type: ignore[type-arg]
    """Register all broker reconciliation jobs. All times are IST."""

    scheduler.add_job(
        broker_reconciliation_job,
        trigger="interval",
        minutes=5,
        id="broker_reconciliation",
        name="Broker Reconciliation (5 min)",
        replace_existing=True,
    )
    logger.info(
        "Registered job: broker_reconciliation (every 5 min, market-hours gated)"
    )

    scheduler.add_job(
        broker_reconciliation_eod_job,
        trigger="cron",
        day_of_week="mon-fri",
        hour=15,
        minute=20,
        id="broker_reconciliation_eod",
        name="Broker Reconciliation (EOD)",
        replace_existing=True,
    )
    logger.info("Registered job: broker_reconciliation_eod (Mon–Fri 15:20 IST)")
