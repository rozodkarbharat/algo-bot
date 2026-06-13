"""
APScheduler jobs for the live signal engine.

Jobs defined here:
  live_market_open_init       — 9:10 AM IST  (warm caches / fetch shortlist)
  live_signal_engine_start    — 9:15 AM IST  (subscribe + activate engine)
  live_signal_engine_stop     — 11:30 AM IST (close entry window — keep engine
                                              running for candle observability)
  live_session_cleanup        — 3:30 PM IST  (full stop + state reset)

Schedule rationale:
  9:10 — Pre-market warm-up: pull shortlist, log readiness.
  9:15 — Subscribe to ticks & activate signal engine. The first 15-min candle
         starts forming.
  11:30 — Stop accepting new entries (per strategy spec) by deactivating the
          signal engine. The market engine itself can keep aggregating candles
          for live charts.
  15:30 — Market close: stop the engine, flush partials, clear intraday state.

Registration:
  register_live_engine_jobs() is called from scheduler/scheduler.py at startup.
"""

from app.utils.logger import get_logger

logger = get_logger(__name__)


# ── Job 1: Pre-market initialisation ──────────────────────────────────────────

async def live_market_open_init() -> None:
    """
    Warm-up at 9:10 AM IST: log shortlist size and confirm engine is idle.

    Intentionally non-destructive — actual subscriptions happen at 9:15.
    """
    logger.info("=== Live market open init started ===")
    try:
        from app.services.live_signal_service import live_signal_service
        from app.services.shortlist_service import ShortlistService

        shortlist_svc = ShortlistService()
        shortlist = await shortlist_svc.generate_shortlist()
        tradable_count = sum(1 for e in shortlist.entries if e.tradable)
        logger.info(
            "Live init: shortlist has %d tradable / %d candidates for %s; engine.running=%s",
            tradable_count,
            len(shortlist.entries),
            shortlist.target_date,
            live_signal_service.engine.running,
        )
    except Exception as exc:
        logger.error("Live market open init failed: %s", exc, exc_info=True)


# ── Job 2: Signal engine start ────────────────────────────────────────────────

async def live_signal_engine_start() -> None:
    """Start the live signal engine at 9:15 AM IST."""
    logger.info("=== Live signal engine start ===")
    try:
        from app.services.live_signal_service import live_signal_service

        result = await live_signal_service.start()
        logger.info(
            "Live engine start: started=%s, symbols=%d, message=%s",
            result.started, len(result.subscribed_symbols), result.message,
        )
    except Exception as exc:
        logger.error("Live signal engine start failed: %s", exc, exc_info=True)


# ── Job 3: Signal engine shutdown (entry window close) ───────────────────────

async def live_signal_engine_stop() -> None:
    """
    Close the entry window at 11:30 AM IST.

    We deactivate signal generation but leave the market engine running so
    live charts and state continue updating until session cleanup at 15:30.
    """
    logger.info("=== Live signal engine entry-window close ===")
    try:
        from app.services.live_signal_service import live_signal_service

        live_signal_service.engine.signal_engine.deactivate()
        logger.info(
            "Live engine: signal generation deactivated; "
            "candle aggregation continues until session cleanup."
        )
    except Exception as exc:
        logger.error("Live signal engine stop failed: %s", exc, exc_info=True)


# ── Job 4: Health heartbeat ──────────────────────────────────────────────────

async def live_health_heartbeat() -> None:
    """
    Broadcast a live-engine health snapshot to the dashboard.

    Fires every minute between 9:15–15:30 IST so the UI can render a
    status pill without polling the API. The job no-ops cheaply if the
    engine is offline (it still publishes status=OFFLINE).
    """
    try:
        from app.services.live_signal_service import live_signal_service
        payload = await live_signal_service.broadcast_health_heartbeat()
        if payload["status"] != "OK":
            logger.warning("Live engine health: %s — %s", payload["status"], payload["notes"])
    except Exception as exc:
        logger.error("Live health heartbeat failed: %s", exc, exc_info=True)


# ── Job 5: Session cleanup ────────────────────────────────────────────────────

async def live_session_cleanup() -> None:
    """Full daily reset at 3:30 PM IST."""
    logger.info("=== Live session cleanup ===")
    try:
        from app.services.live_signal_service import live_signal_service

        deleted = await live_signal_service.reset_daily()
        logger.info("Live session cleanup complete: %d state rows cleared.", deleted)
    except Exception as exc:
        logger.error("Live session cleanup failed: %s", exc, exc_info=True)


# ── Registration helper ───────────────────────────────────────────────────────

def register_live_engine_jobs(scheduler) -> None:  # type: ignore[type-arg]
    """
    Register all live-engine jobs.

    Called once at startup from scheduler/scheduler.py. All times are IST.
    """
    scheduler.add_job(
        live_market_open_init,
        trigger="cron",
        day_of_week="mon-fri",
        hour=9,
        minute=10,
        id="live_market_open_init",
        name="Live Market Open Initialisation",
        replace_existing=True,
    )
    logger.info("Registered job: live_market_open_init (Mon–Fri 09:10 IST)")

    scheduler.add_job(
        live_signal_engine_start,
        trigger="cron",
        day_of_week="mon-fri",
        hour=9,
        minute=15,
        id="live_signal_engine_start",
        name="Live Signal Engine Start",
        replace_existing=True,
    )
    logger.info("Registered job: live_signal_engine_start (Mon–Fri 09:15 IST)")

    scheduler.add_job(
        live_signal_engine_stop,
        trigger="cron",
        day_of_week="mon-fri",
        hour=11,
        minute=30,
        id="live_signal_engine_stop",
        name="Live Signal Engine Entry-Window Close",
        replace_existing=True,
    )
    logger.info("Registered job: live_signal_engine_stop (Mon–Fri 11:30 IST)")

    scheduler.add_job(
        live_session_cleanup,
        trigger="cron",
        day_of_week="mon-fri",
        hour=15,
        minute=30,
        id="live_session_cleanup",
        name="Live Session Cleanup",
        replace_existing=True,
    )
    logger.info("Registered job: live_session_cleanup (Mon–Fri 15:30 IST)")

    # Health heartbeat — every minute, 9:15–15:30 IST. The job itself is a
    # no-op when the engine is offline so off-hours runs are harmless.
    scheduler.add_job(
        live_health_heartbeat,
        trigger="cron",
        day_of_week="mon-fri",
        hour="9-15",
        minute="*/1",
        id="live_health_heartbeat",
        name="Live Engine Health Heartbeat",
        replace_existing=True,
    )
    logger.info("Registered job: live_health_heartbeat (Mon–Fri 09:00–15:59 IST, every minute)")
