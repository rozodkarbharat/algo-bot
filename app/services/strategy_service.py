"""
Strategy service — orchestrates one-side day detection and probability calculation.

Responsibilities:
  1. Fetch historical candles from the candle repository.
  2. Run OneSideDayDetector (pure strategy engine) per day per symbol.
  3. Persist OneSideDay results to MongoDB.
  4. Feed persisted results into ContinuationProbabilityEngine.
  5. Upsert ContinuationStatistic documents.
  6. Return structured progress summaries.

Architecture rules enforced here:
  - Service calls repositories only — never Beanie/Motor directly.
  - Strategy engine (detector, probability engine) is called here with plain data.
  - No broker imports — strategy is completely broker-independent.
  - Routes call this service; they never touch repositories or the detector.
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

from app.config.settings import settings
from app.core.exceptions import DatabaseException, StrategyException
from app.models.continuation_statistic import ContinuationStatistic
from app.models.historical_candle import CandleData
from app.models.one_side_day import OneSideDay
from app.repositories.continuation_statistic_repository import ContinuationStatisticRepository
from app.repositories.historical_candle_repository import HistoricalCandleRepository
from app.repositories.one_side_day_repository import OneSideDayRepository
from app.repositories.stock_repository import StockRepository
from app.services.stock_universe_service import StockUniverseService
from app.strategy.continuation_probability import ContinuationProbabilityEngine
from app.strategy.one_side_detector import OneSideDayDetector
from app.strategy.strategy_registry import registry as strategy_registry
from app.utils.analytics import group_candles_by_trading_date, validate_candle_sequence
from app.utils.candle_intervals import CandleInterval
from app.utils.logger import get_logger
from app.utils.market_time import date_to_utc_midnight, utc_midnight_to_date
from app.utils.trading_day import get_trading_days, last_completed_trading_day

logger = get_logger(__name__)


@dataclass
class DetectionSummary:
    """Result returned after running OSD detection for a date range / symbol set."""

    total_symbols: int = 0
    total_days: int = 0
    one_side_days: int = 0
    choppy_days: int = 0
    invalid_days: int = 0
    records_written: int = 0
    failed_symbols: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "total_symbols": self.total_symbols,
            "total_days": self.total_days,
            "one_side_days": self.one_side_days,
            "choppy_days": self.choppy_days,
            "invalid_days": self.invalid_days,
            "records_written": self.records_written,
            "failed_symbols": self.failed_symbols,
            "duration_seconds": round(self.duration_seconds, 2),
        }


@dataclass
class ProbabilitySummary:
    """Result returned after recalculating continuation statistics."""

    total_symbols: int = 0
    tradable_symbols: int = 0
    failed_symbols: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "total_symbols": self.total_symbols,
            "tradable_symbols": self.tradable_symbols,
            "failed_symbols": self.failed_symbols,
            "duration_seconds": round(self.duration_seconds, 2),
        }


class StrategyService:
    """
    Orchestrates historical day classification and continuation probability.

    Strategy-aware: the day classifier and probability engine are resolved
    from the StrategyRegistry so future strategies can plug in without
    modifying this service.

    Typical call flow for bootstrapping from historical data:
        svc = StrategyService()                         # One-Side ORB (default)
        svc = StrategyService(strategy_id="gap_breakout")  # future strategy
        detection = await svc.run_detection_range(
            from_date=date(2020, 1, 1),
            to_date=date.today(),
        )
        prob = await svc.calculate_all_continuation_stats()
    """

    def __init__(self, strategy_id: str = "one_side_orb") -> None:
        self._strategy_id = strategy_id
        self._osd_repo = OneSideDayRepository()
        self._cont_repo = ContinuationStatisticRepository()
        self._candle_repo = HistoricalCandleRepository()
        self._stock_repo = StockRepository()
        self._universe_svc = StockUniverseService()

        # Resolve classifier via registry — supports any registered strategy.
        # Falls back to direct OneSideDayDetector construction for robustness
        # during early startup before the registry is fully initialised.
        try:
            strategy = strategy_registry.get(strategy_id)
            classifier = strategy.create_day_classifier()
            # Use the native detector for the existing OneSideDay persistence path
            self._detector: OneSideDayDetector = (
                classifier._detector
                if hasattr(classifier, "_detector")
                else OneSideDayDetector(min_move_percent=settings.OSD_MIN_MOVE_PERCENT)
            )
        except (KeyError, Exception):
            self._detector = OneSideDayDetector(
                min_move_percent=settings.OSD_MIN_MOVE_PERCENT
            )

        self._prob_engine = ContinuationProbabilityEngine(
            lookback_days=settings.OSD_LOOKBACK_DAYS,
            min_occurrences=settings.OSD_MIN_OCCURRENCES,
            probability_threshold=settings.OSD_CONTINUATION_THRESHOLD,
        )

    # ── One-Side Day Detection ────────────────────────────────────────────────

    async def run_detection_for_date(
        self,
        trading_date: date,
        symbols: Optional[list[str]] = None,
    ) -> DetectionSummary:
        """
        Run OSD detection for all (or specified) symbols on a single trading date.

        Args:
            trading_date: The NSE trading date to classify.
            symbols: Optional symbol filter; defaults to all active NIFTY50 stocks.

        Returns:
            DetectionSummary with counts and timing.
        """
        logger.info("OSD detection for %s starting ...", trading_date)
        t0 = time.monotonic()
        summary = DetectionSummary()

        if symbols is None:
            stocks = await self._universe_svc.get_active_stocks()
            symbols = [s.symbol for s in stocks]

        summary.total_symbols = len(symbols)
        summary.total_days = len(symbols)  # one day per symbol

        for symbol in symbols:
            try:
                record = await self._detect_and_build_record(symbol, trading_date)
                if record is None:
                    summary.invalid_days += 1
                    continue

                await self._osd_repo.upsert_record(record)
                summary.records_written += 1

                if record.is_one_side:
                    summary.one_side_days += 1
                elif record.opposite_side_crossed:
                    summary.choppy_days += 1
                else:
                    summary.invalid_days += 1

            except Exception as exc:
                logger.error(
                    "Detection failed for %s on %s: %s", symbol, trading_date, exc,
                    exc_info=True,
                )
                summary.failed_symbols.append(symbol)
                summary.invalid_days += 1

        summary.duration_seconds = time.monotonic() - t0
        logger.info(
            "OSD detection %s: %d one-side / %d choppy / %d invalid | %.1fs",
            trading_date,
            summary.one_side_days,
            summary.choppy_days,
            summary.invalid_days,
            summary.duration_seconds,
        )
        return summary

    async def run_detection_range(
        self,
        from_date: date,
        to_date: date,
        symbols: Optional[list[str]] = None,
        batch_size: int = 10,
    ) -> DetectionSummary:
        """
        Run OSD detection for a date range across all (or specified) symbols.

        Processes symbols in batches to limit concurrent DB activity.
        Progress is logged after each batch.

        Args:
            from_date: Start of historical range (inclusive).
            to_date: End of historical range (inclusive).
            symbols: Optional symbol filter; defaults to all active stocks.
            batch_size: Number of symbols to process concurrently.

        Returns:
            Aggregated DetectionSummary.
        """
        logger.info(
            "OSD range detection %s → %s starting ...", from_date, to_date
        )
        t0 = time.monotonic()
        aggregate = DetectionSummary()

        if symbols is None:
            stocks = await self._universe_svc.get_active_stocks()
            symbols = [s.symbol for s in stocks]

        aggregate.total_symbols = len(symbols)
        trading_days = get_trading_days(from_date, to_date)
        aggregate.total_days = len(symbols) * len(trading_days)

        # Process in concurrent batches to avoid saturating MongoDB.
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i : i + batch_size]
            results = await asyncio.gather(
                *[self._detect_symbol_range(symbol, from_date, to_date) for symbol in batch],
                return_exceptions=True,
            )
            for symbol, result in zip(batch, results):
                if isinstance(result, Exception):
                    logger.error("Batch failed for %s: %s", symbol, result, exc_info=False)
                    aggregate.failed_symbols.append(symbol)
                else:
                    aggregate.one_side_days += result.one_side_days
                    aggregate.choppy_days += result.choppy_days
                    aggregate.invalid_days += result.invalid_days
                    aggregate.records_written += result.records_written

            logger.info(
                "OSD range: processed %d/%d symbols ...",
                min(i + batch_size, len(symbols)),
                len(symbols),
            )

        aggregate.duration_seconds = time.monotonic() - t0
        logger.info(
            "OSD range detection complete: %d records | %d one-side | %.1fs",
            aggregate.records_written,
            aggregate.one_side_days,
            aggregate.duration_seconds,
        )
        return aggregate

    # ── Continuation Probability ──────────────────────────────────────────────

    async def calculate_continuation_stats(
        self,
        symbol: str,
        lookback_days: Optional[int] = None,
    ) -> ContinuationStatistic:
        """
        Calculate and persist continuation probability for a single symbol.

        Args:
            symbol: NSE ticker symbol.
            lookback_days: Override the default lookback window. None = use settings.

        Returns:
            The upserted ContinuationStatistic document.
        """
        effective_lookback = lookback_days or settings.OSD_LOOKBACK_DAYS

        # Fetch all OSD records for this symbol (sorted oldest-first by repo).
        to_dt = date_to_utc_midnight(last_completed_trading_day())
        # Use a far-back start date to get all history.
        from_dt = datetime(2015, 1, 1, tzinfo=timezone.utc)
        records = await self._osd_repo.get_between_dates(
            symbol=symbol.upper(),
            from_date=from_dt,
            to_date=to_dt,
        )

        if not records:
            logger.warning("[%s] No OSD records found; skipping probability calculation.", symbol)
            # Return a zero-probability stat rather than raising.
            stat = ContinuationStatistic(symbol=symbol.upper())
            stat.recalculate(
                total=0,
                successes=0,
                lookback_days=effective_lookback,
                min_occurrences=settings.OSD_MIN_OCCURRENCES,
                threshold=settings.OSD_CONTINUATION_THRESHOLD,
            )
            return await self._cont_repo.upsert_statistic(stat)

        # Build (date, is_one_side) history for the engine.
        history: list[tuple[date, bool]] = [
            (utc_midnight_to_date(r.trading_date), r.is_one_side)
            for r in records
        ]

        # Run the pure probability engine (no DB, no I/O).
        engine = ContinuationProbabilityEngine(
            lookback_days=effective_lookback,
            min_occurrences=settings.OSD_MIN_OCCURRENCES,
            probability_threshold=settings.OSD_CONTINUATION_THRESHOLD,
        )
        result = engine.calculate(symbol=symbol.upper(), history=history)

        # Build + upsert the ContinuationStatistic document.
        existing = await self._cont_repo.get_by_symbol(symbol.upper())
        stat = existing or ContinuationStatistic(symbol=symbol.upper())
        stat.recalculate(
            total=result.total_occurrences,
            successes=result.continuation_successes,
            lookback_days=effective_lookback,
            min_occurrences=settings.OSD_MIN_OCCURRENCES,
            threshold=settings.OSD_CONTINUATION_THRESHOLD,
        )
        if result.rejection_reason:
            stat.metadata["rejection_reason"] = result.rejection_reason

        return await self._cont_repo.upsert_statistic(stat)

    async def calculate_all_continuation_stats(
        self,
        symbols: Optional[list[str]] = None,
        lookback_days: Optional[int] = None,
    ) -> ProbabilitySummary:
        """
        Recalculate continuation statistics for all (or specified) symbols.

        Designed to be called nightly after the EOD OSD detection job.
        """
        t0 = time.monotonic()
        summary = ProbabilitySummary()

        if symbols is None:
            stocks = await self._universe_svc.get_active_stocks()
            symbols = [s.symbol for s in stocks]

        summary.total_symbols = len(symbols)

        for symbol in symbols:
            try:
                stat = await self.calculate_continuation_stats(symbol, lookback_days)
                if stat.tradable:
                    summary.tradable_symbols += 1
            except Exception as exc:
                logger.error(
                    "Probability calculation failed for %s: %s", symbol, exc, exc_info=True
                )
                summary.failed_symbols.append(symbol)

        summary.duration_seconds = time.monotonic() - t0
        logger.info(
            "Probability update complete: %d tradable / %d total | %.1fs",
            summary.tradable_symbols,
            summary.total_symbols,
            summary.duration_seconds,
        )
        return summary

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _detect_and_build_record(
        self, symbol: str, trading_date: date
    ) -> Optional[OneSideDay]:
        """
        Fetch candles for (symbol, date) and return an OneSideDay document.

        Returns None if no candle data is available for the date.
        """
        trading_dt = date_to_utc_midnight(trading_date)
        candles = await self._candle_repo.get_candles_between_dates(
            symbol=symbol,
            interval=str(CandleInterval.FIFTEEN_MINUTE),
            from_date=trading_dt,
            to_date=trading_dt,
        )

        if not candles:
            logger.debug("[%s] No candle bucket for %s — skipping.", symbol, trading_date)
            return None

        day_candles: list[CandleData] = []
        for bucket in candles:
            day_candles.extend(bucket.candles)
        day_candles.sort(key=lambda c: c.time)

        validation_error = validate_candle_sequence(day_candles)
        if validation_error:
            logger.warning("[%s] Candle validation error on %s: %s", symbol, trading_date, validation_error)

        result = self._detector.detect(day_candles)

        record = OneSideDay(
            symbol=symbol.upper(),
            trading_date=trading_dt,
            is_one_side=result.is_one_side,
            direction=result.direction,
            first_candle_high=result.first_candle_high,
            first_candle_low=result.first_candle_low,
            breakout_price=result.breakout_price,
            breakout_time=result.breakout_time,
            move_percent=result.move_percent,
            opposite_side_crossed=result.opposite_side_crossed,
            continuation_candidate=result.continuation_candidate,
            metadata={
                "candle_count": result.candle_count,
                "rejection_reason": result.rejection_reason,
            },
        )
        return record

    async def _detect_symbol_range(
        self, symbol: str, from_date: date, to_date: date
    ) -> DetectionSummary:
        """Detect OSD for a single symbol across the full date range. Returns per-symbol summary."""
        summary = DetectionSummary(total_symbols=1)
        trading_days = get_trading_days(from_date, to_date)
        records: list[OneSideDay] = []

        for trading_date in trading_days:
            try:
                record = await self._detect_and_build_record(symbol, trading_date)
                if record is None:
                    summary.invalid_days += 1
                    continue

                records.append(record)
                summary.total_days += 1

                if record.is_one_side:
                    summary.one_side_days += 1
                elif record.opposite_side_crossed:
                    summary.choppy_days += 1
                else:
                    summary.invalid_days += 1

            except Exception as exc:
                logger.error(
                    "Detection error for %s on %s: %s", symbol, trading_date, exc
                )
                summary.invalid_days += 1

        if records:
            written = await self._osd_repo.bulk_upsert(records)
            summary.records_written = written

        logger.debug(
            "[%s] Detected %d/%d days — %d one-side, %d choppy, %d invalid.",
            symbol,
            len(records),
            len(trading_days),
            summary.one_side_days,
            summary.choppy_days,
            summary.invalid_days,
        )
        return summary
