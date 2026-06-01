"""
Historical data ingestion service.

Orchestrates the full pipeline:
  1. Determine which symbols to sync
  2. For each symbol, find the date range with missing data
  3. Fetch OHLCV candles from Angel One in async batches
  4. Persist day-buckets to MongoDB
  5. Write audit logs for every symbol
  6. Return a structured summary

Design principles:
  - Services call repositories only — never Beanie/Motor directly.
  - Services call broker clients (angel_one_historical) — never raw HTTP.
  - Concurrency is controlled here, not in the broker client.
  - Failures for one symbol never abort the entire batch.
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from app.brokers.angelone.historical_data import angel_one_historical
from app.core.exceptions import AngelOneAuthException, IngestionException
from app.models.historical_candle import CandleData, HistoricalCandle
from app.models.market_data_sync_log import MarketDataSyncLog, SyncStatus
from app.models.stock import Stock
from app.repositories.historical_candle_repository import HistoricalCandleRepository
from app.repositories.market_data_sync_log_repository import MarketDataSyncLogRepository
from app.repositories.stock_repository import StockRepository
from app.services.stock_universe_service import StockUniverseService
from app.utils.candle_intervals import CandleInterval
from app.utils.logger import get_logger
from app.utils.market_time import date_to_utc_midnight, utc_midnight_to_date
from app.utils.trading_day import get_trading_days, is_trading_day, last_completed_trading_day

logger = get_logger(__name__)


@dataclass
class SyncResult:
    """Aggregate result returned after a full ingestion run."""

    total_symbols: int = 0
    successful: int = 0
    failed: int = 0
    skipped: int = 0
    records_inserted: int = 0
    duration_seconds: float = 0.0
    failed_symbols: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total_symbols": self.total_symbols,
            "successful": self.successful,
            "failed": self.failed,
            "skipped": self.skipped,
            "records_inserted": self.records_inserted,
            "duration_seconds": round(self.duration_seconds, 2),
            "failed_symbols": self.failed_symbols,
        }


class HistoricalDataService:
    """
    Ingests historical OHLCV candles for one or many symbols.

    Typical call flow:
        service = HistoricalDataService()
        result = await service.sync_historical_data(
            from_date=date(2024, 1, 1),
            to_date=date(2024, 3, 31),
            interval=CandleInterval.FIFTEEN_MINUTE,
        )
    """

    def __init__(self) -> None:
        self._candle_repo = HistoricalCandleRepository()
        self._log_repo = MarketDataSyncLogRepository()
        self._stock_repo = StockRepository()
        self._universe_svc = StockUniverseService()

    # ── Public API ────────────────────────────────────────────────────────────

    async def sync_historical_data(
        self,
        from_date: date,
        to_date: date,
        interval: CandleInterval = CandleInterval.FIFTEEN_MINUTE,
        symbols: Optional[list[str]] = None,
        force_refetch: bool = False,
        concurrency: Optional[int] = None,
    ) -> SyncResult:
        """
        Sync historical candles for a list of symbols (or all active stocks).

        Args:
            from_date:     Start date (inclusive).
            to_date:       End date (inclusive). Must be <= last completed trading day.
            interval:      Candle interval to fetch.
            symbols:       Specific symbols; None = all active stocks.
            force_refetch: If True, overwrite dates that already exist.
            concurrency:   Override the default INGESTION_CONCURRENCY setting.
        """
        from app.config.settings import settings

        start_wall = time.monotonic()
        result = SyncResult()

        # Clamp to_date to last completed trading day.
        max_date = last_completed_trading_day()
        if to_date > max_date:
            logger.info("Clamping to_date from %s to %s (last completed trading day).", to_date, max_date)
            to_date = max_date

        if from_date > to_date:
            logger.warning("from_date %s > to_date %s — nothing to sync.", from_date, to_date)
            return result

        # Resolve symbol list.
        stocks = await self._resolve_stocks(symbols)
        if not stocks:
            logger.warning("No active stocks found to sync.")
            return result

        result.total_symbols = len(stocks)
        max_concurrent = concurrency or settings.INGESTION_CONCURRENCY

        logger.info(
            "Starting historical sync: %d symbols, %s → %s, interval=%s, concurrency=%d",
            len(stocks), from_date, to_date, interval, max_concurrent,
        )

        # Process symbols in parallel with a semaphore.
        semaphore = asyncio.Semaphore(max_concurrent)

        async def sync_one(stock: Stock) -> tuple[str, int, bool]:
            """Returns (symbol, records_inserted, success)."""
            async with semaphore:
                return await self._sync_symbol(
                    stock=stock,
                    from_date=from_date,
                    to_date=to_date,
                    interval=interval,
                    force_refetch=force_refetch,
                )

        tasks = [asyncio.create_task(sync_one(stock)) for stock in stocks]
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)

        for stock, outcome in zip(stocks, outcomes):
            if isinstance(outcome, Exception):
                result.failed += 1
                result.failed_symbols.append(stock.symbol)
                logger.error("Sync failed for %s: %s", stock.symbol, outcome)
            else:
                symbol, inserted, success = outcome
                if success:
                    if inserted == 0:
                        result.skipped += 1
                    else:
                        result.successful += 1
                        result.records_inserted += inserted
                else:
                    result.failed += 1
                    result.failed_symbols.append(symbol)

        result.duration_seconds = time.monotonic() - start_wall
        logger.info(
            "Sync complete: %d ok / %d skipped / %d failed | %d buckets inserted | %.1fs",
            result.successful, result.skipped, result.failed,
            result.records_inserted, result.duration_seconds,
        )
        return result

    async def sync_eod(
        self,
        interval: CandleInterval = CandleInterval.FIFTEEN_MINUTE,
    ) -> SyncResult:
        """
        Convenience method for the EOD scheduler job.

        Syncs yesterday's data (or today's if market just closed) for all
        active stocks. Safe to call multiple times — skips existing dates.
        """
        target = last_completed_trading_day()
        logger.info("EOD sync triggered for trading date: %s", target)
        return await self.sync_historical_data(
            from_date=target,
            to_date=target,
            interval=interval,
        )

    async def get_candles_for_strategy(
        self,
        symbol: str,
        from_date: date,
        to_date: date,
        interval: CandleInterval = CandleInterval.FIFTEEN_MINUTE,
    ) -> list[CandleData]:
        """
        Return a flat, chronologically-ordered list of CandleData for a symbol.

        This is the primary entry point for strategy engines — they receive
        a plain list of candles, not the MongoDB bucket structure.
        """
        from_dt = date_to_utc_midnight(from_date)
        to_dt = date_to_utc_midnight(to_date)
        buckets = await self._candle_repo.get_candles_between_dates(
            symbol=symbol,
            interval=str(interval),
            from_date=from_dt,
            to_date=to_dt,
        )
        # Flatten all day-buckets into a single candle stream.
        all_candles: list[CandleData] = []
        for bucket in buckets:
            all_candles.extend(bucket.candles)
        return sorted(all_candles, key=lambda c: c.time)

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _sync_symbol(
        self,
        stock: Stock,
        from_date: date,
        to_date: date,
        interval: CandleInterval,
        force_refetch: bool,
    ) -> tuple[str, int, bool]:
        """
        Sync one symbol. Returns (symbol, records_inserted, success).

        Creates a sync log, fetches candles, persists buckets, and updates the log.
        """
        symbol = stock.symbol
        from_dt = date_to_utc_midnight(from_date)
        to_dt = date_to_utc_midnight(to_date)

        # Create pending log entry.
        log = MarketDataSyncLog(
            symbol=symbol,
            exchange=stock.exchange,
            interval=str(interval),
            sync_from=from_dt,
            sync_to=to_dt,
            status=SyncStatus.PENDING,
        )
        log = await self._log_repo.create_log(log)

        try:
            log.mark_running()
            await self._log_repo.update_log(log)

            # Find which trading days are missing from DB.
            missing_dates = await self._find_missing_dates(
                symbol=symbol,
                interval=str(interval),
                from_date=from_date,
                to_date=to_date,
                force_refetch=force_refetch,
            )

            if not missing_dates:
                log.mark_skipped(skipped=len(get_trading_days(from_date, to_date)))
                await self._log_repo.update_log(log)
                logger.debug("[%s] All dates already present — skipped.", symbol)
                return symbol, 0, True

            logger.info(
                "[%s] Fetching %d missing trading days (%s → %s)",
                symbol, len(missing_dates), missing_dates[0], missing_dates[-1],
            )

            # Fetch candles from Angel One.
            raw_candles = await angel_one_historical.fetch_historical_candles(
                symbol=symbol,
                instrument_token=stock.instrument_token,
                exchange=stock.exchange,
                interval=interval,
                from_date=missing_dates[0],
                to_date=missing_dates[-1],
            )

            if not raw_candles:
                logger.warning("[%s] API returned no candles for the date range.", symbol)
                log.mark_success(inserted=0, skipped=len(missing_dates))
                await self._log_repo.update_log(log)
                return symbol, 0, True

            # Group candles by trading date and persist one bucket per day.
            inserted = await self._persist_candles_by_day(
                stock=stock,
                candles=raw_candles,
                interval=str(interval),
                expected_dates=set(missing_dates),
            )

            skipped = len(get_trading_days(from_date, to_date)) - inserted
            log.mark_success(inserted=inserted, skipped=max(0, skipped))
            await self._log_repo.update_log(log)

            logger.info("[%s] Sync complete: %d buckets inserted.", symbol, inserted)
            return symbol, inserted, True

        except AngelOneAuthException as exc:
            # Auth failure — propagate up; the batch should not continue.
            log.mark_failed(str(exc))
            await self._log_repo.update_log(log)
            raise

        except Exception as exc:
            log.mark_failed(str(exc))
            await self._log_repo.update_log(log)
            logger.error("[%s] Sync failed: %s", symbol, exc, exc_info=True)
            return symbol, 0, False

    async def _find_missing_dates(
        self,
        symbol: str,
        interval: str,
        from_date: date,
        to_date: date,
        force_refetch: bool,
    ) -> list[date]:
        """
        Return trading days in [from_date, to_date] not yet stored in MongoDB.

        When force_refetch=True, returns all trading days regardless.
        """
        all_trading_days = get_trading_days(from_date, to_date)
        if force_refetch or not all_trading_days:
            return all_trading_days

        from_dt = date_to_utc_midnight(from_date)
        to_dt = date_to_utc_midnight(to_date)
        existing_dts = await self._candle_repo.get_existing_dates(
            symbol=symbol,
            interval=interval,
            from_date=from_dt,
            to_date=to_dt,
        )
        existing_dates = {utc_midnight_to_date(dt) for dt in existing_dts}
        missing = [d for d in all_trading_days if d not in existing_dates]
        return missing

    async def _persist_candles_by_day(
        self,
        stock: Stock,
        candles: list[CandleData],
        interval: str,
        expected_dates: set[date],
    ) -> int:
        """
        Group a flat list of candles by their calendar date and upsert one
        HistoricalCandle bucket per day.

        Returns the count of day-buckets written.
        """
        # Group candles by trading date (IST date of the candle's open time).
        from app.utils.market_time import to_ist

        by_date: dict[date, list[CandleData]] = {}
        for candle in candles:
            trading_date = to_ist(candle.time).date()
            by_date.setdefault(trading_date, []).append(candle)

        inserted = 0
        for trading_date, day_candles in sorted(by_date.items()):
            if not is_trading_day(trading_date):
                continue  # skip weekends that may slip in from the API

            trading_dt = date_to_utc_midnight(trading_date)
            # Sort candles within the day chronologically.
            day_candles.sort(key=lambda c: c.time)

            is_new = await self._candle_repo.save_daily_candles(
                symbol=stock.symbol,
                exchange=stock.exchange,
                interval=interval,
                trading_date=trading_dt,
                candles=day_candles,
            )
            if is_new:
                inserted += 1

        return inserted

    async def _resolve_stocks(self, symbols: Optional[list[str]]) -> list[Stock]:
        """Return Stock documents for the given symbols (or all active stocks)."""
        if symbols:
            results: list[Stock] = []
            for sym in symbols:
                stock = await self._stock_repo.get_stock_by_symbol(sym.upper())
                if stock and stock.is_active:
                    results.append(stock)
                else:
                    logger.warning("Symbol '%s' not found or inactive — skipping.", sym)
            return results
        return await self._universe_svc.get_active_stocks()
