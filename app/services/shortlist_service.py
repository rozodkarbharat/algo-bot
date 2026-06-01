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
from app.utils.trading_day import get_previous_trading_day, last_completed_trading_day

logger = get_logger(__name__)


@dataclass
class ShortlistEntry:
    """A single tradable candidate on today's shortlist."""

    symbol: str
    direction: str                 # "UP" or "DOWN"
    first_candle_high: float
    first_candle_low: float
    breakout_price: Optional[float]
    move_percent: Optional[float]   # yesterday's move (for context)
    continuation_probability: float  # 0.0–1.0
    total_occurrences: int          # historical sample size
    yesterday_date: date
    # Multi-strategy identity (defaulted for backward compatibility)
    strategy_id: str = "one_side_orb"
    strategy_name: str = "One-Side ORB"


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

    def to_dict(self) -> dict:
        return {
            "target_date": self.target_date.isoformat(),
            "yesterday": self.yesterday.isoformat(),
            "strategy_id": self.strategy_id,
            "strategy_name": self.strategy_name,
            "total_candidates": len(self.entries),
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
                         Defaults to today (the day we will actually trade).
            probability_threshold: Override the configured threshold.
            strategy_id: Strategy for which to generate the shortlist.
                         Currently only 'one_side_orb' uses OSD/continuation data;
                         future strategies will have their own data paths.

        Returns:
            ShortlistResult with all qualifying stocks and metadata.
        """
        t0 = time.monotonic()
        effective_date = target_date or last_completed_trading_day()
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
        entries: list[ShortlistEntry] = []

        for osd in one_side_records:
            stat = await self._cont_repo.get_by_symbol(osd.symbol)

            if stat is None:
                logger.debug(
                    "[%s] No continuation stat found; skipping shortlist entry.", osd.symbol
                )
                continue

            if stat.continuation_probability < threshold:
                logger.debug(
                    "[%s] Probability %.1f%% < threshold %.1f%%; excluded.",
                    osd.symbol,
                    stat.continuation_probability * 100,
                    threshold * 100,
                )
                continue

            if not stat.tradable:
                # Double-check: tradable flag incorporates both threshold AND min_occurrences.
                logger.debug("[%s] tradable=False (insufficient sample size); excluded.", osd.symbol)
                continue

            entries.append(
                ShortlistEntry(
                    symbol=osd.symbol,
                    direction=osd.direction or "UP",
                    first_candle_high=osd.first_candle_high,
                    first_candle_low=osd.first_candle_low,
                    breakout_price=osd.breakout_price,
                    move_percent=osd.move_percent,
                    continuation_probability=stat.continuation_probability,
                    total_occurrences=stat.total_occurrences,
                    yesterday_date=yesterday,
                    strategy_id=strategy_id,
                    strategy_name=strat_name,
                )
            )

        # Step 3: Sort by probability descending (highest edge first).
        entries.sort(key=lambda e: e.continuation_probability, reverse=True)
        result.entries = entries
        result.duration_seconds = time.monotonic() - t0

        logger.info(
            "Shortlist for %s: %d tradable candidates (%.1fs)",
            effective_date, len(entries), result.duration_seconds,
        )
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
    ) -> ShortlistResult:
        """
        Execute one shortlist generation. Raises ConflictException if another
        run is already in flight. Reuses `ShortlistService.generate_shortlist()`
        — no business logic lives here.
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
                    last_total_shortlisted=len(result.entries),
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
