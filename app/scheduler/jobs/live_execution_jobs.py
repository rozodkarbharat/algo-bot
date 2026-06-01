"""
APScheduler jobs for the live execution engine.

Jobs registered here:
  live_broker_session_refresh   — 08:30 IST  (pre-market re-auth)
  live_session_warmup           — 09:14 IST  (hydrate book before signals start)
  live_order_reconcile          — every 30 s during market hours (09:15–15:30)
  live_position_reconcile       — every 5 min during market hours
  live_halt_monitor             — every 1 min during market hours
  live_eod_close_all            — 15:15 IST  (force-close every live position)

Schedule rationale:
  - Session refresh runs early so trades aren't blocked by a stale JWT.
  - Order reconciliation is high-frequency because partial fills need to
    be visible in near-real-time.
  - Position reconciliation runs slower (5 min) because it triggers
    operator alerts on mismatch — too noisy at 30 s.

Registration is invoked from scheduler/scheduler.py::_register_all_jobs().
"""

from app.utils.logger import get_logger

logger = get_logger(__name__)


# ── Job 1: Pre-market broker session refresh ─────────────────────────────────

async def live_broker_session_refresh() -> None:
    """Force a fresh broker login so the JWT is warm at open."""
    logger.info("=== Live broker session refresh ===")
    try:
        from app.services.live_execution_service import live_execution_service
        ok = await live_execution_service.refresh_broker_session()
        logger.info("Live broker session refresh: success=%s", ok)
    except Exception as exc:
        logger.error("Live broker session refresh failed: %s", exc, exc_info=True)


# ── Job 2: Pre-market live engine warmup ─────────────────────────────────────

async def live_session_warmup() -> None:
    """Hydrate the in-memory live position book before signals start."""
    logger.info("=== Live execution warmup ===")
    try:
        from app.services.live_execution_service import live_execution_service
        await live_execution_service.ensure_ready()
        open_count = live_execution_service.position_manager.open_count
        logger.info("Live warmup: %d open positions recovered.", open_count)
    except Exception as exc:
        logger.error("Live execution warmup failed: %s", exc, exc_info=True)


# ── Job 3: High-frequency order reconciliation ───────────────────────────────

async def live_order_reconcile() -> None:
    """Refresh every non-terminal LiveOrder status against the broker."""
    try:
        from app.services.live_execution_service import live_execution_service
        from app.utils.market_time import is_market_open
        if not is_market_open():
            return
        result = await live_execution_service.reconcile_orders()
        if result.get("transitions", 0) > 0:
            logger.info(
                "Live order reconcile: checked=%d transitions=%d",
                result.get("checked", 0), result.get("transitions", 0),
            )
    except Exception as exc:
        logger.error("Live order reconcile failed: %s", exc, exc_info=True)


# ── Job 4: Slower position reconciliation ────────────────────────────────────

async def live_position_reconcile() -> None:
    """Cross-check the in-memory book against broker-held positions."""
    try:
        from app.services.live_execution_service import live_execution_service
        from app.utils.market_time import is_market_open
        if not is_market_open():
            return
        diffs = await live_execution_service.reconcile_positions()
        if diffs:
            logger.warning(
                "Live position reconcile found %d discrepancies", len(diffs)
            )
    except Exception as exc:
        logger.error("Live position reconcile failed: %s", exc, exc_info=True)


# ── Job 5: Live halt monitor (daily loss / drawdown auto-pause) ─────────────

async def live_halt_monitor() -> None:
    """Periodically reassess halt criteria even without a triggering trade."""
    try:
        from app.services.live_execution_service import live_execution_service
        from app.utils.market_time import is_market_open
        if not is_market_open():
            return
        await live_execution_service._check_post_trade_halts()  # noqa: SLF001
    except Exception as exc:
        logger.error("Live halt monitor failed: %s", exc, exc_info=True)


# ── Job 6: EOD force-close ───────────────────────────────────────────────────

async def live_eod_close_all() -> None:
    """Force-close every open live position at EOD."""
    logger.info("=== Live EOD close-all ===")
    try:
        from app.models.live_position import LiveExitReason
        from app.services.live_execution_service import live_execution_service
        result = await live_execution_service.close_all_open(
            reason=LiveExitReason.EOD_EXIT
        )
        logger.info(
            "Live EOD close-all: %d positions flattened (reason=%s).",
            result.closed, result.reason,
        )
    except Exception as exc:
        logger.error("Live EOD close-all failed: %s", exc, exc_info=True)


# ── Registration helper ──────────────────────────────────────────────────────

def register_live_execution_jobs(scheduler) -> None:  # type: ignore[type-arg]
    """Register all live-execution jobs. All times are IST."""
    from app.config.settings import settings

    poll_seconds = max(5.0, settings.LIVE_EXEC_ORDER_POLL_INTERVAL_SECONDS)

    scheduler.add_job(
        live_broker_session_refresh,
        trigger="cron",
        day_of_week="mon-fri",
        hour=8,
        minute=30,
        id="live_broker_session_refresh",
        name="Live Broker Session Refresh",
        replace_existing=True,
    )
    logger.info("Registered job: live_broker_session_refresh (Mon–Fri 08:30 IST)")

    scheduler.add_job(
        live_session_warmup,
        trigger="cron",
        day_of_week="mon-fri",
        hour=9,
        minute=14,
        id="live_session_warmup",
        name="Live Execution Warmup",
        replace_existing=True,
    )
    logger.info("Registered job: live_session_warmup (Mon–Fri 09:14 IST)")

    scheduler.add_job(
        live_order_reconcile,
        trigger="interval",
        seconds=poll_seconds,
        id="live_order_reconcile",
        name="Live Order Reconciliation",
        replace_existing=True,
    )
    logger.info(
        "Registered job: live_order_reconcile (every %ds, market-hours gated)",
        int(poll_seconds),
    )

    scheduler.add_job(
        live_position_reconcile,
        trigger="interval",
        minutes=5,
        id="live_position_reconcile",
        name="Live Position Reconciliation",
        replace_existing=True,
    )
    logger.info(
        "Registered job: live_position_reconcile (every 5 min, market-hours gated)"
    )

    scheduler.add_job(
        live_halt_monitor,
        trigger="interval",
        minutes=1,
        id="live_halt_monitor",
        name="Live Halt Monitor",
        replace_existing=True,
    )
    logger.info(
        "Registered job: live_halt_monitor (every 1 min, market-hours gated)"
    )

    scheduler.add_job(
        live_eod_close_all,
        trigger="cron",
        day_of_week="mon-fri",
        hour=15,
        minute=15,
        id="live_eod_close_all",
        name="Live EOD Close-All",
        replace_existing=True,
    )
    logger.info("Registered job: live_eod_close_all (Mon–Fri 15:15 IST)")
