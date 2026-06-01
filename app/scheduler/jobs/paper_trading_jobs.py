"""
APScheduler jobs for the paper trading engine.

Jobs registered here:
  paper_session_warmup    — 09:14 IST  (boot/recover account + open positions)
  paper_eod_close_all     — 15:15 IST  (force-close every open paper position)
  paper_daily_reset       — 15:35 IST  (reset per-day counters + unpause)

Schedule rationale:
  9:14  — Right before live engine startup, ensure paper account exists and
          any OPEN positions persisted from yesterday are hydrated.
  15:15 — Matches PAPER_EOD_EXIT_TIME_IST default; the candle-driven exit
          logic also acts at this time, but the scheduled job guarantees a
          force-close even on symbols that never receive a late candle.
  15:35 — After live session cleanup (15:30), reset paper account daily
          counters so tomorrow starts clean.

Registration is invoked from scheduler/scheduler.py::_register_all_jobs().
"""

from app.utils.logger import get_logger

logger = get_logger(__name__)


# ── Job 1: Pre-market paper warmup ───────────────────────────────────────────

async def paper_session_warmup() -> None:
    """Ensure the paper account exists and recover any OPEN positions."""
    logger.info("=== Paper session warmup ===")
    try:
        from app.services.paper_trading_service import paper_trading_service

        account = await paper_trading_service.ensure_ready()
        open_count = paper_trading_service.position_manager.open_count
        logger.info(
            "Paper warmup: account=%s available_capital=%.2f open_positions=%d is_paused=%s",
            account.account_id, account.available_capital, open_count, account.is_paused,
        )
    except Exception as exc:
        logger.error("Paper session warmup failed: %s", exc, exc_info=True)


# ── Job 2: EOD force-close ────────────────────────────────────────────────────

async def paper_eod_close_all() -> None:
    """Force-close every open paper position at PAPER_EOD_EXIT_TIME_IST."""
    logger.info("=== Paper EOD close-all ===")
    try:
        from app.services.paper_trading_service import paper_trading_service

        result = await paper_trading_service.close_all_open()
        logger.info("Paper EOD close-all: %d positions closed (reason=%s).",
                    result.closed, result.reason)
    except Exception as exc:
        logger.error("Paper EOD close-all failed: %s", exc, exc_info=True)


# ── Job 3: Daily reset ───────────────────────────────────────────────────────

async def paper_daily_reset() -> None:
    """Reset per-day counters and clear any auto-paused state."""
    logger.info("=== Paper daily reset ===")
    try:
        from app.services.paper_trading_service import paper_trading_service

        account = await paper_trading_service.reset_daily()
        logger.info(
            "Paper daily reset: account=%s realized_pnl=%.2f total_trades=%d",
            account.account_id, account.realized_pnl, account.total_trades,
        )
    except Exception as exc:
        logger.error("Paper daily reset failed: %s", exc, exc_info=True)


# ── Registration helper ───────────────────────────────────────────────────────

def register_paper_trading_jobs(scheduler) -> None:  # type: ignore[type-arg]
    """Register all paper-trading jobs. All times are IST."""
    scheduler.add_job(
        paper_session_warmup,
        trigger="cron",
        day_of_week="mon-fri",
        hour=9,
        minute=14,
        id="paper_session_warmup",
        name="Paper Trading Session Warmup",
        replace_existing=True,
    )
    logger.info("Registered job: paper_session_warmup (Mon–Fri 09:14 IST)")

    scheduler.add_job(
        paper_eod_close_all,
        trigger="cron",
        day_of_week="mon-fri",
        hour=15,
        minute=15,
        id="paper_eod_close_all",
        name="Paper Trading EOD Close-All",
        replace_existing=True,
    )
    logger.info("Registered job: paper_eod_close_all (Mon–Fri 15:15 IST)")

    scheduler.add_job(
        paper_daily_reset,
        trigger="cron",
        day_of_week="mon-fri",
        hour=15,
        minute=35,
        id="paper_daily_reset",
        name="Paper Trading Daily Reset",
        replace_existing=True,
    )
    logger.info("Registered job: paper_daily_reset (Mon–Fri 15:35 IST)")
