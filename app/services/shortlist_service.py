"""
Shortlist service — daily tradable candidate generation.

Logic:
  1. Find all stocks where yesterday was a one-side day.
  2. Look up each stock's continuation probability.
  3. If probability >= threshold → add to tradable shortlist.
  4. Sort by probability (highest edge first).
  5. Return the shortlist with full context for signal generation.

The shortlist is generated on-demand AND stored to MongoDB so the API can
serve the pre-computed result without re-querying on every request.

Architecture:
  - This service calls OneSideDayRepository and ContinuationStatisticRepository.
  - It does NOT touch the strategy engine directly (that's StrategyService's job).
  - Routes call this service; they never touch repositories.
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

from app.config.settings import settings
from app.core.exceptions import ConflictException
from app.models.continuation_statistic import ContinuationStatistic
from app.models.one_side_day import OneSideDay
from app.repositories.continuation_statistic_repository import ContinuationStatisticRepository
from app.repositories.one_side_day_repository import OneSideDayRepository
from app.services.stock_universe_service import StockUniverseService
from app.utils.logger import get_logger
from app.utils.market_time import date_to_utc_midnight
from app.utils.trading_day import (
    get_previous_trading_day,
    last_completed_trading_day,
    upcoming_trading_session,
)

logger = get_logger(__name__)


@dataclass
class ShortlistEntry:
    """
    A single candidate on today's shortlist.

    Contains both *tradable* candidates (passing the probability + sample-size
    bar) and *skipped* ones — UI consumers display the latter with a "Skipped"
    badge plus `reason_skipped`, while the live signal engine filters down to
    `tradable=True` before subscribing. Keeping rejects in the list lets the
    operator see why a one-side stock didn't make the actionable cut.
    """

    symbol: str
    direction: str                 # "UP" or "DOWN"
    first_candle_high: float
    first_candle_low: float
    breakout_price: Optional[float]
    move_percent: Optional[float]   # yesterday's move (for context)
    continuation_probability: float  # 0.0–1.0 (0.0 if no continuation stat exists)
    total_occurrences: int          # historical sample size (0 if no stat exists)
    yesterday_date: date
    # Multi-strategy identity (defaulted for backward compatibility)
    strategy_id: str = "one_side_orb"
    strategy_name: str = "One-Side ORB"
    # Tradability — populated by generate_shortlist; defaults preserve the
    # legacy "tradable-only" contract for any callers constructing entries
    # directly.
    tradable: bool = True
    reason_skipped: Optional[str] = None


@dataclass
class ShortlistPipelineMetrics:
    """Telemetry from the optional full-pipeline path (sync + detect + stats)."""

    data_date: Optional[date] = None
    candles_synced: int = 0
    sync_failed_symbols: list[str] = field(default_factory=list)
    osd_one_side_days: int = 0
    tradable_symbols: int = 0


@dataclass
class ShortlistResult:
    """Result returned by generate_shortlist()."""

    target_date: date                         # date this shortlist is for
    yesterday: date                           # the one-side day that triggered entries
    entries: list[ShortlistEntry] = field(default_factory=list)
    total_candidates_checked: int = 0
    threshold_used: float = 0.0
    duration_seconds: float = 0.0
    strategy_id: str = "one_side_orb"
    strategy_name: str = "One-Side ORB"
    pipeline_metrics: Optional[ShortlistPipelineMetrics] = None

    def tradable_entries(self) -> list["ShortlistEntry"]:
        """Subset of entries that passed all gating checks (probability + sample size)."""
        return [e for e in self.entries if e.tradable]

    def to_dict(self) -> dict:
        tradable = [e for e in self.entries if e.tradable]
        return {
            "target_date": self.target_date.isoformat(),
            "yesterday": self.yesterday.isoformat(),
            "strategy_id": self.strategy_id,
            "strategy_name": self.strategy_name,
            "total_candidates": len(self.entries),
            "total_tradable": len(tradable),
            "total_checked": self.total_candidates_checked,
            "threshold": self.threshold_used,
            "duration_seconds": round(self.duration_seconds, 3),
            "entries": [
                {
                    "symbol": e.symbol,
                    "direction": e.direction,
                    "first_candle_high": e.first_candle_high,
                    "first_candle_low": e.first_candle_low,
                    "breakout_price": e.breakout_price,
                    "move_percent": e.move_percent,
                    "continuation_probability": round(e.continuation_probability * 100, 2),
                    "total_occurrences": e.total_occurrences,
                    "yesterday_date": e.yesterday_date.isoformat(),
                    "strategy_id": e.strategy_id,
                    "tradable": e.tradable,
                    "reason_skipped": e.reason_skipped,
                }
                for e in self.entries
            ],
        }


class ShortlistService:
    """
    Generates the daily tradable shortlist from yesterday's one-side days.

    Typical usage:
        svc = ShortlistService()
        shortlist = await svc.generate_shortlist()  # uses yesterday automatically
        for entry in shortlist.entries:
            print(entry.symbol, entry.direction, f"{entry.continuation_probability:.1%}")
    """

    def __init__(self) -> None:
        self._osd_repo = OneSideDayRepository()
        self._cont_repo = ContinuationStatisticRepository()
        self._universe_svc = StockUniverseService()

    # ── Public API ────────────────────────────────────────────────────────────

    async def generate_shortlist(
        self,
        target_date: Optional[date] = None,
        probability_threshold: Optional[float] = None,
        strategy_id: str = "one_side_orb",
    ) -> ShortlistResult:
        """
        Generate the tradable shortlist for target_date.

        Algorithm:
          - yesterday = last completed trading day before target_date
          - Find all stocks where yesterday was a one-side day
          - Filter: continuation_probability >= threshold
          - Sort by probability descending

        Args:
            target_date: The trading day to generate a shortlist for.
                         Defaults to the upcoming trading session (the day we
                         are currently in or about to trade) so that a morning
                         or in-session view/run targets *today's* tradable list
                         rather than yesterday's. See
                         ``upcoming_trading_session`` for the exact resolution.
            probability_threshold: Override the configured threshold.
            strategy_id: Strategy for which to generate the shortlist.
                         Currently only 'one_side_orb' uses OSD/continuation data;
                         future strategies will have their own data paths.

        Returns:
            ShortlistResult with all qualifying stocks and metadata.
        """
        t0 = time.monotonic()
        effective_date = target_date or upcoming_trading_session()
        yesterday = get_previous_trading_day(effective_date)
        threshold = probability_threshold or settings.OSD_CONTINUATION_THRESHOLD

        # Resolve strategy metadata for labelling
        from app.strategy.strategy_registry import registry as _reg
        try:
            _strat = _reg.get(strategy_id)
            strat_name = _strat.strategy_name
        except KeyError:
            strat_name = strategy_id

        logger.info(
            "Generating shortlist for %s (strategy=%s, yesterday=%s, threshold=%.0f%%)",
            effective_date, strategy_id, yesterday, threshold * 100,
        )

        result = ShortlistResult(
            target_date=effective_date,
            yesterday=yesterday,
            threshold_used=threshold,
            strategy_id=strategy_id,
            strategy_name=strat_name,
        )

        # Step 1: Find yesterday's one-side days.
        yesterday_dt = date_to_utc_midnight(yesterday)
        osd_records = await self._osd_repo.get_by_date(yesterday_dt)
        one_side_records = [r for r in osd_records if r.is_one_side]

        result.total_candidates_checked = len(one_side_records)
        logger.info(
            "Shortlist: %d stocks had a one-side day yesterday (%s).",
            len(one_side_records), yesterday,
        )

        if not one_side_records:
            result.duration_seconds = time.monotonic() - t0
            return result

        # Step 2: Look up continuation probability for each candidate.
        # We build entries for EVERY one-side stock so the UI can show the
        # rejected ones with a "Skipped" badge + reason. Tradability is
        # decided per-entry; downstream consumers (live signal engine, etc.)
        # filter by `tradable=True` before acting.
        entries: list[ShortlistEntry] = []

        # Batch-fetch all continuation stats in a single query. Doing one
        # get_by_symbol() per record is an N+1 pattern: against a remote
        # cluster each lookup costs a full network round-trip (~80ms), so a
        # high one-side-day count (e.g. 160+ stocks) blows past the request
        # timeout. One $in query keeps this flat regardless of count.
        stat_by_symbol = await self._cont_repo.get_by_symbols(
            [osd.symbol for osd in one_side_records]
        )

        for osd in one_side_records:
            stat = stat_by_symbol.get(osd.symbol.upper())

            tradable: bool
            reason: Optional[str]
            prob: float
            occurrences: int

            if stat is None:
                tradable = False
                reason = "No continuation statistic available yet"
                prob = 0.0
                occurrences = 0
                logger.debug("[%s] No continuation stat found; entry kept as skipped.", osd.symbol)
            else:
                prob = stat.continuation_probability
                occurrences = stat.total_occurrences

                if prob < threshold:
                    tradable = False
                    reason = (
                        f"Probability {prob * 100:.1f}% below threshold "
                        f"{threshold * 100:.1f}%"
                    )
                elif not stat.tradable:
                    # `stat.tradable` already incorporates min-occurrences; surface
                    # that distinct reason rather than the generic threshold copy.
                    tradable = False
                    reason = (
                        f"Insufficient sample size ({occurrences} occurrences)"
                    )
                else:
                    tradable = True
                    reason = None

            entries.append(
                ShortlistEntry(
                    symbol=osd.symbol,
                    direction=osd.direction or "UP",
                    first_candle_high=osd.first_candle_high,
                    first_candle_low=osd.first_candle_low,
                    breakout_price=osd.breakout_price,
                    move_percent=osd.move_percent,
                    continuation_probability=prob,
                    total_occurrences=occurrences,
                    yesterday_date=yesterday,
                    strategy_id=strategy_id,
                    strategy_name=strat_name,
                    tradable=tradable,
                    reason_skipped=reason,
                )
            )

        # Step 3: Sort tradable-first (highest edge first), then skipped rows
        # (also by probability desc) so the actionable picks always appear at
        # the top of the table without needing a UI-side filter.
        entries.sort(
            key=lambda e: (not e.tradable, -e.continuation_probability),
        )
        result.entries = entries
        result.duration_seconds = time.monotonic() - t0

        tradable_count = sum(1 for e in entries if e.tradable)
        logger.info(
            "Shortlist for %s: %d tradable / %d candidates (%.1fs)",
            effective_date, tradable_count, len(entries), result.duration_seconds,
        )
        return result

    async def run_full_pipeline(
        self,
        target_date: Optional[date] = None,
        probability_threshold: Optional[float] = None,
        strategy_id: str = "one_side_orb",
    ) -> ShortlistResult:
        """
        End-to-end fallback pipeline used by `POST /api/v1/shortlist/run` when
        the daily scheduler chain (15:45 sync / 16:00 OSD / 16:15 stats / 16:30
        shortlist) has not run.

        This is the manual-recovery path: if the previous evening's scheduled
        run was missed, an operator can trigger this the next morning (pre-market
        or intraday) to build the current session's shortlist on demand. With
        no `target_date`, it targets `upcoming_trading_session()` and pulls the
        already-complete previous session's candles as `data_date`.

        Steps for `data_date = previous trading day of target_date`:
          1. Fetch 15-min candles for `data_date` from Angel One (via
             `HistoricalDataService.sync_historical_data`).
          2. Run OSD detection for `data_date`.
          3. Recompute continuation statistics for all active stocks.
          4. Generate the shortlist for `target_date` (delegates to
             `generate_shortlist`).

        The composed `ShortlistResult` carries a `pipeline_metrics` payload so
        callers can surface what was actually fetched/detected.
        """
        # Local imports keep this method optional and avoid import cycles.
        from app.services.historical_data_service import HistoricalDataService
        from app.services.strategy_service import StrategyService
        from app.utils.candle_intervals import CandleInterval

        effective_target = target_date or upcoming_trading_session()
        data_date = get_previous_trading_day(effective_target)

        logger.info(
            "Shortlist full pipeline starting: target=%s data_date=%s strategy=%s",
            effective_target, data_date, strategy_id,
        )

        metrics = ShortlistPipelineMetrics(data_date=data_date)

        # Step 1: Pull candles for data_date from Angel One.
        try:
            data_svc = HistoricalDataService()
            sync_result = await data_svc.sync_historical_data(
                from_date=data_date,
                to_date=data_date,
                interval=CandleInterval.FIFTEEN_MINUTE,
            )
            metrics.candles_synced = sync_result.records_inserted
            metrics.sync_failed_symbols = list(sync_result.failed_symbols)
            logger.info(
                "Pipeline sync for %s: %d ok / %d skipped / %d failed | %d buckets",
                data_date,
                sync_result.successful,
                sync_result.skipped,
                sync_result.failed,
                sync_result.records_inserted,
            )
        except Exception as exc:
            # Don't abort the pipeline on sync issues — the DB may already have
            # the data. Surface the error in metrics for visibility.
            logger.error("Pipeline sync step failed: %s", exc, exc_info=True)
            metrics.sync_failed_symbols.append(f"__sync_step__: {exc}")

        # Step 2: OSD detection for data_date.
        strategy_svc = StrategyService(strategy_id=strategy_id)
        try:
            detection = await strategy_svc.run_detection_for_date(
                trading_date=data_date,
            )
            metrics.osd_one_side_days = detection.one_side_days
            logger.info(
                "Pipeline OSD detection for %s: %d one-side / %d records written",
                data_date, detection.one_side_days, detection.records_written,
            )
        except Exception as exc:
            logger.error("Pipeline OSD detection failed: %s", exc, exc_info=True)

        # Step 3: Recompute continuation stats for the universe.
        try:
            prob = await strategy_svc.calculate_all_continuation_stats()
            metrics.tradable_symbols = prob.tradable_symbols
            logger.info(
                "Pipeline probability update: %d tradable / %d total",
                prob.tradable_symbols, prob.total_symbols,
            )
        except Exception as exc:
            logger.error("Pipeline probability update failed: %s", exc, exc_info=True)

        # Step 4: Generate the shortlist using the freshly-updated DB.
        result = await self.generate_shortlist(
            target_date=effective_target,
            probability_threshold=probability_threshold,
            strategy_id=strategy_id,
        )
        result.pipeline_metrics = metrics
        return result

    async def get_tradable_stocks(self) -> list[ContinuationStatistic]:
        """
        Return all stocks currently flagged as tradable (probability >= threshold).

        Useful for monitoring which symbols have statistical edge.
        """
        return await self._cont_repo.get_tradable_stocks()

    async def get_yesterday_one_side_days(
        self, reference_date: Optional[date] = None
    ) -> list[OneSideDay]:
        """Return all one-side day records from yesterday relative to reference_date."""
        ref = reference_date or last_completed_trading_day()
        yesterday = get_previous_trading_day(ref)
        yesterday_dt = date_to_utc_midnight(yesterday)
        records = await self._osd_repo.get_by_date(yesterday_dt)
        return [r for r in records if r.is_one_side]


# ── Shortlist Run Manager ─────────────────────────────────────────────────────
#
# Thin singleton wrapper around `ShortlistService.generate_shortlist()` that
# tracks "is a run in progress?" plus the last run's outcome. Used by:
#   * The 16:30 IST APScheduler job (`daily_shortlist_generation`)
#   * The manual `POST /api/v1/shortlist/run` endpoint
#
# Both paths funnel through the same `run()` method so business logic is not
# duplicated and status is consistent across automatic and manual triggers.
# An asyncio.Lock guarantees single-flight execution: a second run started
# while one is in flight raises ConflictException.


@dataclass
class ShortlistRunSnapshot:
    """Public state of the shortlist run manager."""

    running: bool
    last_status: str  # "idle" | "running" | "success" | "error"
    last_started_at: Optional[datetime] = None
    last_finished_at: Optional[datetime] = None
    last_target_date: Optional[date] = None
    last_total_checked: int = 0
    last_total_shortlisted: int = 0
    last_duration_seconds: Optional[float] = None
    last_error: Optional[str] = None
    last_trigger: Optional[str] = None  # "manual" | "scheduler"

    def to_dict(self) -> dict:
        return {
            "running": self.running,
            "last_status": self.last_status,
            "last_started_at": self.last_started_at.isoformat() if self.last_started_at else None,
            "last_finished_at": self.last_finished_at.isoformat() if self.last_finished_at else None,
            "last_target_date": self.last_target_date.isoformat() if self.last_target_date else None,
            "last_total_checked": self.last_total_checked,
            "last_total_shortlisted": self.last_total_shortlisted,
            "last_duration_seconds": (
                round(self.last_duration_seconds, 3) if self.last_duration_seconds is not None else None
            ),
            "last_error": self.last_error,
            "last_trigger": self.last_trigger,
        }


class ShortlistRunManager:
    """Process-wide single-flight runner for shortlist generation."""
    
    def __init__(self, service: Optional[ShortlistService] = None) -> None:
        self._service = service or ShortlistService()
        self._lock = asyncio.Lock()
        self._state = ShortlistRunSnapshot(running=False, last_status="idle")

    @property
    def is_running(self) -> bool:
        return self._state.running

    def snapshot(self) -> ShortlistRunSnapshot:
        return self._state

    async def run(
        self,
        target_date: Optional[date] = None,
        probability_threshold: Optional[float] = None,
        trigger: str = "manual",
        full_pipeline: bool = False,
    ) -> ShortlistResult:
        """
        Execute one shortlist generation. Raises ConflictException if another
        run is already in flight.

        If `full_pipeline=True`, runs the end-to-end fallback chain
        (Angel One sync → OSD detection → probability update → shortlist).
        Otherwise calls `ShortlistService.generate_shortlist()` directly,
        which is a fast read against MongoDB and the path used by the
        16:30 IST scheduler job.
        """
        if self._state.running:
            raise ConflictException(
                "A shortlist run is already in progress.",
                detail={
                    "started_at": (
                        self._state.last_started_at.isoformat()
                        if self._state.last_started_at
                        else None
                    ),
                    "trigger": self._state.last_trigger,
                },
            )

        async with self._lock:
            # Re-check after acquiring the lock (race-safety).
            if self._state.running:
                raise ConflictException("A shortlist run is already in progress.")

            self._state = ShortlistRunSnapshot(
                running=True,
                last_status="running",
                last_started_at=datetime.now(timezone.utc),
                last_target_date=target_date,
                last_trigger=trigger,
            )
            try:
                if full_pipeline:
                    result = await self._service.run_full_pipeline(
                        target_date=target_date,
                        probability_threshold=probability_threshold,
                    )
                else:
                    result = await self._service.generate_shortlist(
                        target_date=target_date,
                        probability_threshold=probability_threshold,
                    )
                self._state = ShortlistRunSnapshot(
                    running=False,
                    last_status="success",
                    last_started_at=self._state.last_started_at,
                    last_finished_at=datetime.now(timezone.utc),
                    last_target_date=result.target_date,
                    last_total_checked=result.total_candidates_checked,
                    last_total_shortlisted=sum(1 for e in result.entries if e.tradable),
                    last_duration_seconds=result.duration_seconds,
                    last_trigger=trigger,
                )
                return result
            except Exception as exc:
                self._state = ShortlistRunSnapshot(
                    running=False,
                    last_status="error",
                    last_started_at=self._state.last_started_at,
                    last_finished_at=datetime.now(timezone.utc),
                    last_target_date=self._state.last_target_date,
                    last_error=str(exc),
                    last_trigger=trigger,
                )
                logger.exception("Shortlist run failed: %s", exc)
                raise


# Process-wide singleton consumed by routes AND scheduler jobs.
shortlist_run_manager = ShortlistRunManager()
