"""
APScheduler jobs for market data ingestion.

Jobs defined here:
  eod_candle_sync         — 3:45 PM IST daily (after market close)
  pre_market_sync_check   — 8:30 AM IST daily (morning readiness check)

Registration:
  register_market_data_jobs() is called from scheduler/scheduler.py
  at application startup after APScheduler is started.

Job design:
  - All jobs are async coroutines (AsyncIOExecutor handles them).
  - Jobs are idempotent — safe to run multiple times.
  - Failures are logged but do not crash the scheduler.
  - coalesce=True (set globally) collapses missed runs into one.
"""

import asyncio
from datetime import date

from app.utils.candle_intervals import CandleInterval
from app.utils.logger import get_logger
from app.utils.trading_day import last_completed_trading_day, today_ist

logger = get_logger(__name__)


# ── EOD Candle Sync ───────────────────────────────────────────────────────────

async def eod_candle_sync() -> None:
    """
    End-of-Day candle sync — runs at 3:45 PM IST after market close.

    Fetches today's 15-minute candles for all active NIFTY50 stocks and
    persists them to MongoDB. Skips symbols already up-to-date.

    Triggered: daily at 15:45 IST (Monday–Friday).
    """
    logger.info("=== EOD Candle Sync job started ===")
    try:
        # Lazy import to avoid circular imports at module load time.
        from app.services.historical_data_service import HistoricalDataService

        service = HistoricalDataService()
        result = await service.sync_eod(interval=CandleInterval.FIFTEEN_MINUTE)

        logger.info(
            "=== EOD Sync complete: %d ok / %d skipped / %d failed | %d buckets | %.1fs ===",
            result.successful, result.skipped, result.failed,
            result.records_inserted, result.duration_seconds,
        )
        if result.failed_symbols:
            logger.warning("Failed symbols: %s", result.failed_symbols)

    except Exception as exc:
        logger.error("EOD candle sync job failed with unhandled error: %s", exc, exc_info=True)


# ── Pre-Market Sync Check ─────────────────────────────────────────────────────

async def pre_market_sync_check() -> None:
    """
    Morning pre-market readiness check — runs at 8:30 AM IST.

    Verifies that yesterday's data is present in MongoDB for all active stocks.
    If any symbols are missing, triggers a backfill sync so the strategy
    engine starts with complete data at market open.

    Triggered: daily at 08:30 IST (Monday–Friday).
    """
    logger.info("=== Pre-market sync check started ===")
    try:
        from app.repositories.historical_candle_repository import HistoricalCandleRepository
        from app.services.historical_data_service import HistoricalDataService
        from app.services.stock_universe_service import StockUniverseService

        candle_repo = HistoricalCandleRepository()
        universe_svc = StockUniverseService()
        data_svc = HistoricalDataService()

        yesterday = last_completed_trading_day()
        stocks = await universe_svc.get_active_stocks()

        # Find which symbols are missing yesterday's data.
        missing_symbols: list[str] = []
        for stock in stocks:
            latest = await candle_repo.get_latest_candle_date(
                symbol=stock.symbol,
                interval=str(CandleInterval.FIFTEEN_MINUTE),
            )
            if latest is None or latest.date() < yesterday:
                missing_symbols.append(stock.symbol)

        if not missing_symbols:
            logger.info("Pre-market check: all %d symbols are up-to-date.", len(stocks))
            return

        logger.warning(
            "Pre-market check: %d symbols missing data for %s — triggering backfill: %s",
            len(missing_symbols), yesterday, missing_symbols,
        )

        result = await data_svc.sync_historical_data(
            from_date=yesterday,
            to_date=yesterday,
            interval=CandleInterval.FIFTEEN_MINUTE,
            symbols=missing_symbols,
        )
        logger.info(
            "Pre-market backfill: %d synced / %d failed.",
            result.successful, result.failed,
        )

    except Exception as exc:
        logger.error("Pre-market sync check failed: %s", exc, exc_info=True)


# ── Registration helper ───────────────────────────────────────────────────────

def register_market_data_jobs(scheduler) -> None:  # type: ignore[type-arg]
    """
    Register all market-data jobs with the provided APScheduler instance.

    Called once at application startup from scheduler/scheduler.py.
    All times are in IST (Asia/Kolkata) — the scheduler was initialised
    with that timezone.
    """
    # EOD sync — 3:45 PM IST, Monday to Friday
    scheduler.add_job(
        eod_candle_sync,
        trigger="cron",
        day_of_week="mon-fri",
        hour=15,
        minute=45,
        id="eod_candle_sync",
        name="EOD 15-min Candle Sync",
        replace_existing=True,
    )
    logger.info("Registered job: eod_candle_sync (Mon–Fri 15:45 IST)")

    # Pre-market check — 8:30 AM IST, Monday to Friday
    scheduler.add_job(
        pre_market_sync_check,
        trigger="cron",
        day_of_week="mon-fri",
        hour=8,
        minute=30,
        id="pre_market_sync_check",
        name="Pre-Market Data Check",
        replace_existing=True,
    )
    logger.info("Registered job: pre_market_sync_check (Mon–Fri 08:30 IST)")
