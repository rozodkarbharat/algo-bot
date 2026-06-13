"""
ORHV Service — orchestrates all three phases of the Opening Range Historical
Validation strategy with full database I/O.

Responsibilities:
  Phase 1 — Setup Detection:
    * Fetch 15-min candles for a given (symbol, date).
    * Run ORHVSetupDetector (pure logic).
    * Persist ORHVSetup documents.

  Phase 2 — Historical Validation:
    * Load all prior ORHVSetup candidates for a symbol.
    * Load candle history for the D+1 simulation.
    * Run ORHVHistoricalValidator (pure logic).
    * Persist ORHVValidationRecord documents.
    * Update ORHVStatistics per symbol.

  Phase 3 — Signal Delivery (live):
    * Return today's tradable ORHVValidationRecord list so the live engine
      can build its shortlist (ORHVCandidate objects).

  Backtest orchestration:
    * Thin wrapper: BacktestService calls strategy.create_backtest_engine()
      which returns ORHVBacktestEngine; this service is NOT used for backtesting.

Architecture:
  - Service calls repositories; never Beanie/Motor directly.
  - Pure engines are called here with pre-fetched data.
  - No broker imports.
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from app.repositories.historical_candle_repository import HistoricalCandleRepository
from app.repositories.orhv_setup_repository import ORHVSetupRepository
from app.repositories.orhv_statistics_repository import ORHVStatisticsRepository
from app.repositories.orhv_validation_repository import ORHVValidationRepository
from app.repositories.orhv_signal_repository import ORHVSignalRepository
from app.services.stock_universe_service import StockUniverseService
from app.strategy.strategies.opening_range_historical_validation.config import ORHVConfig
from app.core.exceptions import ConflictException
from app.strategy.strategies.opening_range_historical_validation.constants import (
    STRATEGY_ID,
    STRATEGY_NAME,
)
from app.strategy.strategies.opening_range_historical_validation.detector import (
    ORHVDetectionResult,
    ORHVSetupDetector,
)
from app.strategy.strategies.opening_range_historical_validation.historical_validator import (
    ORHVHistoricalValidator,
)
from app.strategy.strategies.opening_range_historical_validation.models import (
    ORHVSetup,
    ORHVStatistics,
    ORHVValidationRecord,
)
from app.strategy.strategies.opening_range_historical_validation.signal_generator import (
    ORHVCandidate,
)
from app.utils.candle_intervals import CandleInterval
from app.utils.logger import get_logger
from app.utils.market_time import date_to_utc_midnight, utc_midnight_to_date
from app.utils.trading_day import (
    get_next_trading_day,
    get_previous_trading_day,
    get_trading_days,
    last_completed_trading_day,
)

logger = get_logger(__name__)


# ── Summary dataclasses ───────────────────────────────────────────────────────

@dataclass
class ORHVDetectionSummary:
    total_symbols: int = 0
    candidates_found: int = 0
    rejected: int = 0
    no_data: int = 0
    failed_symbols: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "total_symbols": self.total_symbols,
            "candidates_found": self.candidates_found,
            "rejected": self.rejected,
            "no_data": self.no_data,
            "failed_symbols": self.failed_symbols,
            "duration_seconds": round(self.duration_seconds, 2),
        }


@dataclass
class ORHVValidationSummary:
    total_candidates: int = 0
    tradable: int = 0
    not_tradable: int = 0
    insufficient_history: int = 0
    failed_symbols: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "total_candidates": self.total_candidates,
            "tradable": self.tradable,
            "not_tradable": self.not_tradable,
            "insufficient_history": self.insufficient_history,
            "failed_symbols": self.failed_symbols,
            "duration_seconds": round(self.duration_seconds, 2),
        }


# ── Service ───────────────────────────────────────────────────────────────────

@dataclass
class ORHVShortlistEntry:
    symbol: str
    candidate_date: date
    execution_date: date
    orh_d: float
    orl_d: float
    orb_range_pct: float
    win_rate: float
    wins: int
    losses: int
    occurrences_used: int
    occurrences_available: int
    is_candidate: bool = False
    tradable: bool = True
    reason_skipped: Optional[str] = None


@dataclass
class ORHVHistoryCoverage:
    """Result of the pre-validation history guard (ensure_history_available)."""

    lookback_start: date
    lookback_end: date
    expected_trading_days: int = 0
    candle_days_present: int = 0
    candle_days_synced: int = 0
    detection_days_run: int = 0
    candles_inserted: int = 0
    sync_failed_symbols: list[str] = field(default_factory=list)
    sufficient: bool = False

    def to_dict(self) -> dict:
        return {
            "lookback_start": self.lookback_start.isoformat(),
            "lookback_end": self.lookback_end.isoformat(),
            "expected_trading_days": self.expected_trading_days,
            "candle_days_present": self.candle_days_present,
            "candle_days_synced": self.candle_days_synced,
            "detection_days_run": self.detection_days_run,
            "candles_inserted": self.candles_inserted,
            "sync_failed_symbols": self.sync_failed_symbols,
            "sufficient": self.sufficient,
        }


@dataclass
class ORHVPipelineMetrics:
    data_date: Optional[date] = None
    candles_synced: int = 0
    sync_failed_symbols: list[str] = field(default_factory=list)
    candidates_found: int = 0
    validation_tradable: int = 0
    coverage: Optional["ORHVHistoryCoverage"] = None


@dataclass
class ORHVShortlistResult:
    execution_date: date
    candidate_date: date
    entries: list[ORHVShortlistEntry] = field(default_factory=list)
    total_candidates_checked: int = 0
    total_phase1_scanned: int = 0
    threshold_used: float = 0.0
    duration_seconds: float = 0.0
    pipeline_metrics: Optional[ORHVPipelineMetrics] = None


@dataclass
class ORHVSymbolRunResult:
    """Outcome of testing a single symbol (full pipeline or Phase 2 only)."""

    symbol: str
    mode: str
    candidate_date: date
    execution_date: Optional[date]
    has_phase1_setup: bool
    is_candidate: bool
    phase1_reason: Optional[str]
    validated: bool
    occurrences_available: int
    occurrences_used: int
    wins: int
    losses: int
    win_rate: float
    tradable: bool
    reason: Optional[str]
    orh_d: Optional[float]
    orl_d: Optional[float]
    candles_synced: int
    history_candle_days: int
    history_detection_days: int
    duration_seconds: float
    message: str


def _orb_range_pct(orh: float, orl: float) -> float:
    if orl <= 0:
        return 0.0
    return round((orh - orl) / orl * 100, 2)


class ORHVService:
    """
    Orchestrates all ORHV phases with full DB persistence.

    Typical nightly call flow:
        svc = ORHVService()
        detection = await svc.run_detection_for_date(trading_date)
        validation = await svc.run_validation_for_date(trading_date)
        candidates  = await svc.get_tradable_candidates_for_date(next_date)
    """

    def __init__(self, config: Optional[ORHVConfig] = None) -> None:
        self._cfg = config or ORHVConfig()
        self._setup_repo = ORHVSetupRepository()
        self._val_repo = ORHVValidationRepository()
        self._signal_repo = ORHVSignalRepository()
        self._stats_repo = ORHVStatisticsRepository()
        self._candle_repo = HistoricalCandleRepository()
        self._universe_svc = StockUniverseService()
        self._detector = ORHVSetupDetector()
        self._validator = ORHVHistoricalValidator(self._cfg)

    # ── Phase 1: Setup Detection ──────────────────────────────────────────────

    async def run_detection_for_date(
        self,
        trading_date: date,
        symbols: Optional[list[str]] = None,
    ) -> ORHVDetectionSummary:
        """
        Run Phase 1 setup detection for all (or specified) symbols on trading_date.

        Called nightly after market close to populate orhv_setups collection.
        """
        t0 = time.monotonic()
        summary = ORHVDetectionSummary()

        if symbols is None:
            stocks = await self._universe_svc.get_active_stocks()
            symbols = [s.symbol for s in stocks]

        summary.total_symbols = len(symbols)

        for symbol in symbols:
            try:
                result = await self._detect_and_store(symbol, trading_date)
                if result is None:
                    summary.no_data += 1
                elif result.is_candidate:
                    summary.candidates_found += 1
                else:
                    summary.rejected += 1
            except Exception as exc:
                logger.error("[ORHV] Detection failed for %s: %s", symbol, exc, exc_info=True)
                summary.failed_symbols.append(symbol)

        summary.duration_seconds = time.monotonic() - t0
        logger.info(
            "[ORHV] Detection %s: %d candidates / %d rejected / %d no-data in %.1fs",
            trading_date, summary.candidates_found, summary.rejected,
            summary.no_data, summary.duration_seconds,
        )
        return summary

    # ── Phase 2: Historical Validation ────────────────────────────────────────

    async def run_validation_for_date(
        self,
        candidate_date: date,
        symbols: Optional[list[str]] = None,
    ) -> ORHVValidationSummary:
        """
        Run Phase 2 validation for all candidate setups detected on candidate_date.

        For each candidate: load historical prior setups, run validation,
        persist ORHVValidationRecord, update ORHVStatistics.
        """
        t0 = time.monotonic()
        summary = ORHVValidationSummary()

        candidate_dt = date_to_utc_midnight(candidate_date)
        execution_date = candidate_date + timedelta(days=1)
        # Get the next actual trading date
        execution_dt = date_to_utc_midnight(execution_date)

        # Find all candidate setups detected on candidate_date
        candidates = await self._setup_repo.get_candidates_on_date(candidate_dt)
        if symbols:
            syms_upper = {s.upper() for s in symbols}
            candidates = [c for c in candidates if c.symbol in syms_upper]

        summary.total_candidates = len(candidates)

        for setup in candidates:
            try:
                val_result = await self._validate_and_store(
                    setup=setup,
                    candidate_date=candidate_date,
                    execution_dt=execution_dt,
                )
                if val_result.tradable:
                    summary.tradable += 1
                elif "insufficient" in (val_result.rejection_reason or "").lower():
                    summary.insufficient_history += 1
                else:
                    summary.not_tradable += 1
            except Exception as exc:
                logger.error(
                    "[ORHV] Validation failed for %s: %s", setup.symbol, exc, exc_info=True
                )
                summary.failed_symbols.append(setup.symbol)

        summary.duration_seconds = time.monotonic() - t0
        logger.info(
            "[ORHV] Validation %s: %d tradable / %d not-tradable / %d insufficient in %.1fs",
            candidate_date, summary.tradable, summary.not_tradable,
            summary.insufficient_history, summary.duration_seconds,
        )
        return summary

    # ── Phase 3: Candidate list for live engine ────────────────────────────────

    async def get_tradable_candidates_for_date(
        self, trading_date: date
    ) -> list[ORHVCandidate]:
        """
        Return ORHVCandidate objects for tomorrow's live signal engine.

        trading_date = Day D+1 (the execution day).
        """
        trading_dt = date_to_utc_midnight(trading_date)
        validations = await self._val_repo.get_tradable_for_date(trading_dt)

        candidates = []
        for v in validations:
            # Fetch the setup record for ORH_D / ORL_D context
            setup = await self._setup_repo.get_by_symbol_and_date(
                v.symbol, v.candidate_date
            )
            candidates.append(
                ORHVCandidate(
                    symbol=v.symbol,
                    win_rate=v.win_rate,
                    occurrences_used=v.occurrences_used,
                    candidate_date=utc_midnight_to_date(v.candidate_date),
                    orh_d=setup.orh_d if setup else None,
                    orl_d=setup.orl_d if setup else None,
                )
            )

        logger.info(
            "[ORHV] %d tradable candidates for %s.", len(candidates), trading_date
        )
        return candidates

    # ── Convenience: run both phases for a date ────────────────────────────────

    async def run_full_cycle_for_date(
        self, trading_date: date, symbols: Optional[list[str]] = None
    ) -> tuple[ORHVDetectionSummary, ORHVValidationSummary]:
        """Run Phase 1 then Phase 2 for trading_date.  Returns both summaries."""
        detection = await self.run_detection_for_date(trading_date, symbols)
        validation = await self.run_validation_for_date(trading_date, symbols)
        return detection, validation

    # ── Statistics ────────────────────────────────────────────────────────────

    async def get_statistics(self, symbol: Optional[str] = None) -> list[ORHVStatistics]:
        if symbol:
            stat = await self._stats_repo.get_by_symbol(symbol)
            return [stat] if stat else []
        return await self._stats_repo.get_all_sorted_by_win_rate()

    async def get_tradable_statistics(self) -> list[ORHVStatistics]:
        return await self._stats_repo.get_tradable()

    async def generate_shortlist(
        self,
        target_date: Optional[date] = None,
        win_rate_threshold: Optional[float] = None,
    ) -> ORHVShortlistResult:
        """
        Build the ORHV shortlist for execution_date (Day D+1).

        Includes every Phase 1 candidate from candidate_date (Day D), with
        Phase 2 outcomes (tradable or skipped + reason). Read-only against MongoDB.
        """
        t0 = time.monotonic()
        execution_date = target_date or get_next_trading_day(last_completed_trading_day())
        candidate_date = get_previous_trading_day(execution_date)
        threshold = win_rate_threshold or self._cfg.qualification_min_win_rate

        result = ORHVShortlistResult(
            execution_date=execution_date,
            candidate_date=candidate_date,
            threshold_used=threshold,
        )

        candidate_dt = date_to_utc_midnight(candidate_date)
        setups = await self._setup_repo.get_all_on_date(candidate_dt)
        candidates = [s for s in setups if s.is_candidate]
        validations = await self._val_repo.get_for_candidate_date(candidate_dt)
        val_by_symbol = {v.symbol.upper(): v for v in validations}

        result.total_candidates_checked = len(candidates)
        result.total_phase1_scanned = len(setups)
        entries: list[ORHVShortlistEntry] = []

        for setup in setups:
            orh, orl = setup.orh_d, setup.orl_d

            if not setup.is_candidate:
                entries.append(
                    ORHVShortlistEntry(
                        symbol=setup.symbol,
                        candidate_date=candidate_date,
                        execution_date=execution_date,
                        orh_d=orh,
                        orl_d=orl,
                        orb_range_pct=_orb_range_pct(orh, orl),
                        win_rate=0.0,
                        wins=0,
                        losses=0,
                        occurrences_used=0,
                        occurrences_available=0,
                        is_candidate=False,
                        tradable=False,
                        reason_skipped=setup.rejection_reason or "Phase 1 pattern not met",
                    )
                )
                continue

            val = val_by_symbol.get(setup.symbol.upper())

            if val is None:
                entries.append(
                    ORHVShortlistEntry(
                        symbol=setup.symbol,
                        candidate_date=candidate_date,
                        execution_date=execution_date,
                        orh_d=orh,
                        orl_d=orl,
                        orb_range_pct=_orb_range_pct(orh, orl),
                        win_rate=0.0,
                        wins=0,
                        losses=0,
                        occurrences_used=0,
                        occurrences_available=0,
                        is_candidate=True,
                        tradable=False,
                        reason_skipped="Phase 2 validation not run yet",
                    )
                )
                continue

            meets_threshold = val.win_rate >= threshold
            tradable = val.tradable and meets_threshold
            reason: Optional[str] = None
            if not val.tradable:
                reason = val.rejection_reason or "Did not pass historical validation"
            elif not meets_threshold:
                reason = (
                    f"Win rate {val.win_rate * 100:.1f}% below threshold "
                    f"{threshold * 100:.1f}%"
                )

            entries.append(
                ORHVShortlistEntry(
                    symbol=setup.symbol,
                    candidate_date=candidate_date,
                    execution_date=execution_date,
                    orh_d=orh,
                    orl_d=orl,
                    orb_range_pct=_orb_range_pct(orh, orl),
                    win_rate=val.win_rate,
                    wins=val.wins,
                    losses=val.losses,
                    occurrences_used=val.occurrences_used,
                    occurrences_available=val.occurrences_available,
                    is_candidate=True,
                    tradable=tradable,
                    reason_skipped=reason,
                )
            )

        entries.sort(key=lambda e: (not e.tradable, -e.win_rate))
        result.entries = entries
        result.duration_seconds = time.monotonic() - t0

        tradable_n = sum(1 for e in entries if e.tradable)
        logger.info(
            "[ORHV] Shortlist for execution %s: %d tradable / %d candidates (%.1fs)",
            execution_date, tradable_n, len(entries), result.duration_seconds,
        )
        return result

    async def ensure_history_available(
        self,
        data_date: date,
        lookback_days: Optional[int] = None,
        symbols: Optional[list[str]] = None,
    ) -> ORHVHistoryCoverage:
        """
        Guarantee candle + Phase 1 history exists for the lookback window ending
        on (and excluding) data_date, so Phase 2 validation has prior occurrences
        to simulate instead of failing with "0 prior occurrences".

        Steps (all idempotent):
          1. Sync any trading days in the window that lack candle data from the
             broker (HistoricalDataService skips dates already present).
          2. Run Phase 1 detection for window days that have candles but no
             stored ORHVSetup yet (so re-runs only detect new days).

        When ``symbols`` is provided, coverage/sync/detection are scoped to those
        symbols only (used by the single-stock tester). Otherwise the full active
        universe is used.

        data_date itself is intentionally excluded — the caller's pipeline runs
        detection + validation for data_date separately.
        """
        from app.config.settings import settings
        from app.services.historical_data_service import HistoricalDataService
        from app.utils.candle_intervals import CandleInterval

        lookback = lookback_days or settings.ORHV_HISTORY_LOOKBACK_DAYS
        interval = str(CandleInterval.FIFTEEN_MINUTE)
        lookback_start = data_date - timedelta(days=lookback)

        trading_days = [d for d in get_trading_days(lookback_start, data_date) if d < data_date]
        start_dt = date_to_utc_midnight(lookback_start)
        end_dt = date_to_utc_midnight(data_date)

        coverage = ORHVHistoryCoverage(
            lookback_start=lookback_start,
            lookback_end=data_date,
            expected_trading_days=len(trading_days),
        )

        candle_dates = await self._candle_repo.get_distinct_dates(
            interval=interval, from_date=start_dt, to_date=end_dt, symbols=symbols,
        )
        coverage.candle_days_present = len([d for d in candle_dates if d < data_date])

        missing_candle_days = [d for d in trading_days if d not in candle_dates]
        if missing_candle_days:
            logger.info(
                "[ORHV] History guard: %d/%d window trading days missing candles "
                "(%s → %s) — syncing from broker%s.",
                len(missing_candle_days), len(trading_days), lookback_start, data_date,
                f" for {symbols}" if symbols else "",
            )
            try:
                sync_result = await HistoricalDataService().sync_historical_data(
                    from_date=lookback_start,
                    to_date=data_date,
                    interval=CandleInterval.FIFTEEN_MINUTE,
                    symbols=symbols,
                )
                coverage.candles_inserted = sync_result.records_inserted
                coverage.sync_failed_symbols = list(sync_result.failed_symbols)
            except Exception as exc:
                logger.error("[ORHV] History guard sync failed: %s", exc, exc_info=True)
                coverage.sync_failed_symbols.append(f"__sync_step__: {exc}")

            candle_dates = await self._candle_repo.get_distinct_dates(
                interval=interval, from_date=start_dt, to_date=end_dt, symbols=symbols,
            )
            present_now = len([d for d in candle_dates if d < data_date])
            coverage.candle_days_synced = max(0, present_now - coverage.candle_days_present)
            coverage.candle_days_present = present_now

        # Decide which window days still need Phase 1 detection.
        #
        # For a scoped (single-symbol) run we skip days where THAT symbol already
        # has a setup. For a full-universe run we must NOT skip a day just because
        # it has *some* setup — a few stragglers from earlier single-symbol tests
        # would otherwise mask days where the rest of the universe was never
        # detected. Instead we require near-full symbol coverage before treating a
        # day as already detected. Detection is idempotent (upsert), so re-running
        # a partially-covered day is safe.
        window_candle_days = [d for d in candle_dates if d < data_date]
        if symbols:
            setup_dates = await self._setup_repo.get_distinct_setup_dates(
                from_date=start_dt, to_date=end_dt, symbols=symbols,
            )
            detect_days = sorted(d for d in window_candle_days if d not in setup_dates)
        else:
            universe = await self._universe_svc.get_active_stocks()
            universe_size = len(universe) or 1
            min_covered = universe_size * self._cfg.history_coverage_min_fraction
            symbol_counts = await self._setup_repo.get_setup_symbol_counts_by_date(
                from_date=start_dt, to_date=end_dt,
            )
            detect_days = sorted(
                d for d in window_candle_days
                if symbol_counts.get(d, 0) < min_covered
            )
        if detect_days:
            logger.info(
                "[ORHV] History guard: running Phase 1 detection for %d new day(s)%s.",
                len(detect_days), f" ({symbols})" if symbols else "",
            )
            for d in detect_days:
                await self.run_detection_for_date(trading_date=d, symbols=symbols)
        coverage.detection_days_run = len(detect_days)

        coverage.sufficient = coverage.candle_days_present >= self._cfg.min_occurrences_required
        logger.info(
            "[ORHV] History guard complete: %d candle day(s) in window, "
            "%d synced, %d detected, sufficient=%s.",
            coverage.candle_days_present, coverage.candle_days_synced,
            coverage.detection_days_run, coverage.sufficient,
        )
        return coverage

    async def run_full_pipeline(
        self,
        target_date: Optional[date] = None,
        win_rate_threshold: Optional[float] = None,
    ) -> ORHVShortlistResult:
        """
        Ensure history → Phase 1 detect (Day D) → Phase 2 validate → shortlist (D+1).

        When ORHV_AUTO_BACKFILL_ENABLED is set, the history guard first backfills
        and detects the lookback window so Phase 2 has prior occurrences. Otherwise
        only Day D's candles are synced (legacy single-day behaviour).
        """
        from app.config.settings import settings
        from app.services.historical_data_service import HistoricalDataService
        from app.utils.candle_intervals import CandleInterval

        execution_date = target_date or get_next_trading_day(last_completed_trading_day())
        data_date = get_previous_trading_day(execution_date)

        logger.info(
            "[ORHV] Full pipeline: execution=%s data_date=%s",
            execution_date, data_date,
        )

        metrics = ORHVPipelineMetrics(data_date=data_date)

        if settings.ORHV_AUTO_BACKFILL_ENABLED:
            # Backfill + detect the historical lookback window first so Phase 2
            # validation has prior occurrences to simulate.
            coverage = await self.ensure_history_available(data_date=data_date)
            metrics.coverage = coverage
            metrics.candles_synced = coverage.candles_inserted
            metrics.sync_failed_symbols = list(coverage.sync_failed_symbols)

        # Sync Day D candles (the setup day) and run its detection.
        try:
            sync_result = await HistoricalDataService().sync_historical_data(
                from_date=data_date,
                to_date=data_date,
                interval=CandleInterval.FIFTEEN_MINUTE,
            )
            metrics.candles_synced += sync_result.records_inserted
            metrics.sync_failed_symbols.extend(sync_result.failed_symbols)
        except Exception as exc:
            logger.error("[ORHV] Pipeline sync failed: %s", exc, exc_info=True)
            metrics.sync_failed_symbols.append(f"__sync_step__: {exc}")

        detection = await self.run_detection_for_date(trading_date=data_date)
        metrics.candidates_found = detection.candidates_found

        validation = await self.run_validation_for_date(candidate_date=data_date)
        metrics.validation_tradable = validation.tradable

        result = await self.generate_shortlist(
            target_date=execution_date,
            win_rate_threshold=win_rate_threshold,
        )
        result.pipeline_metrics = metrics
        return result

    async def run_symbol_test(
        self,
        symbol: str,
        mode: str = "full",
        target_date: Optional[date] = None,
    ) -> ORHVSymbolRunResult:
        """
        Test the ORHV strategy for a SINGLE symbol.

        mode="full"    — Run Shortlist: ensure history (sync + detect lookback),
                          then sync/detect/validate the setup day. Use when the
                          symbol has no stored data yet.
        mode="phase2"  — Run Phase 2 only: validate against already-stored history
                          to check the symbol's prior performance. No broker calls.

        target_date is the execution day (Day D+1) in full mode, or the candidate
        day (Day D) in phase2 mode. Defaults are derived from the trading calendar.
        """
        from app.config.settings import settings
        from app.services.historical_data_service import HistoricalDataService
        from app.utils.candle_intervals import CandleInterval

        t0 = time.monotonic()
        symbol = symbol.upper()
        coverage: Optional[ORHVHistoryCoverage] = None
        candles_synced = 0

        if mode == "full":
            execution_date = target_date or get_next_trading_day(last_completed_trading_day())
            candidate_date = get_previous_trading_day(execution_date)

            if settings.ORHV_AUTO_BACKFILL_ENABLED:
                coverage = await self.ensure_history_available(
                    data_date=candidate_date, symbols=[symbol]
                )
                candles_synced += coverage.candles_inserted

            try:
                sync_result = await HistoricalDataService().sync_historical_data(
                    from_date=candidate_date,
                    to_date=candidate_date,
                    interval=CandleInterval.FIFTEEN_MINUTE,
                    symbols=[symbol],
                )
                candles_synced += sync_result.records_inserted
            except Exception as exc:
                logger.error("[ORHV] Symbol test sync failed for %s: %s", symbol, exc)

            await self.run_detection_for_date(trading_date=candidate_date, symbols=[symbol])
            await self.run_validation_for_date(candidate_date=candidate_date, symbols=[symbol])
        else:  # phase2
            candidate_date = target_date or last_completed_trading_day()
            execution_date = get_next_trading_day(candidate_date)
            await self.run_validation_for_date(candidate_date=candidate_date, symbols=[symbol])

        # ── Read back the stored Phase 1 + Phase 2 results ────────────────────
        candidate_dt = date_to_utc_midnight(candidate_date)
        setup = await self._setup_repo.get_by_symbol_and_date(symbol, candidate_dt)
        val = await self._val_repo.get_by_symbol_and_date(symbol, candidate_dt)

        has_setup = setup is not None
        is_candidate = bool(setup and setup.is_candidate)
        phase1_reason = setup.rejection_reason if (setup and not setup.is_candidate) else None
        validated = val is not None

        occ_avail = val.occurrences_available if val else 0
        occ_used = val.occurrences_used if val else 0
        wins = val.wins if val else 0
        losses = val.losses if val else 0
        win_rate = val.win_rate if val else 0.0
        tradable = bool(val and val.tradable)
        reason = val.rejection_reason if val else None

        cdate = candidate_date.isoformat()
        if not has_setup:
            if mode == "phase2":
                message = (
                    f"No Phase 1 detection stored for {symbol} on {cdate}. "
                    f"Run Shortlist first to sync candles and detect setups."
                )
            else:
                message = f"No candle data available for {symbol} on {cdate}."
        elif not is_candidate:
            message = (
                f"{symbol} was NOT an ORHV candidate on {cdate}: "
                f"{phase1_reason or 'pattern not met'}."
            )
        elif not validated:
            message = f"{symbol} is a candidate on {cdate} but no validation record was produced."
        elif tradable:
            message = (
                f"{symbol} is TRADABLE — {wins}/{occ_used} wins "
                f"({win_rate * 100:.1f}%) across {occ_avail} prior occurrence(s)."
            )
        else:
            message = f"{symbol} is NOT tradable — {reason or 'did not pass validation'}."

        return ORHVSymbolRunResult(
            symbol=symbol,
            mode=mode,
            candidate_date=candidate_date,
            execution_date=execution_date,
            has_phase1_setup=has_setup,
            is_candidate=is_candidate,
            phase1_reason=phase1_reason,
            validated=validated,
            occurrences_available=occ_avail,
            occurrences_used=occ_used,
            wins=wins,
            losses=losses,
            win_rate=win_rate,
            tradable=tradable,
            reason=reason,
            orh_d=setup.orh_d if setup else None,
            orl_d=setup.orl_d if setup else None,
            candles_synced=candles_synced,
            history_candle_days=coverage.candle_days_present if coverage else 0,
            history_detection_days=coverage.detection_days_run if coverage else 0,
            duration_seconds=round(time.monotonic() - t0, 3),
            message=message,
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _detect_and_store(
        self, symbol: str, trading_date: date
    ) -> Optional[ORHVDetectionResult]:
        """Run Phase 1 for one (symbol, date) and persist result."""
        trading_dt = date_to_utc_midnight(trading_date)
        candle_buckets = await self._candle_repo.get_candles_between_dates(
            symbol=symbol,
            interval=str(CandleInterval.FIFTEEN_MINUTE),
            from_date=trading_dt,
            to_date=trading_dt,
        )
        if not candle_buckets:
            return None

        candles = sorted(
            [c for b in candle_buckets for c in b.candles],
            key=lambda c: c.time,
        )

        result = self._detector.detect(candles)

        doc = ORHVSetup(
            symbol=symbol.upper(),
            setup_date=trading_dt,
            orh_d=result.orh_d,
            orl_d=result.orl_d,
            ch1_found=result.ch1_found,
            ch1_high=result.ch1_high,
            ch1_time=result.ch1_time,
            condition_a_met=result.condition_a_met,
            condition_a_time=result.condition_a_time,
            condition_a_close=result.condition_a_close,
            cl1_found=result.cl1_found,
            cl1_low=result.cl1_low,
            cl1_time=result.cl1_time,
            condition_b_met=result.condition_b_met,
            condition_b_time=result.condition_b_time,
            condition_b_close=result.condition_b_close,
            is_candidate=result.is_candidate,
            rejection_reason=result.rejection_reason,
            candle_count=result.candle_count,
        )
        await self._setup_repo.upsert(doc)
        return result

    async def _validate_and_store(
        self,
        setup: ORHVSetup,
        candidate_date: date,
        execution_dt: datetime,
    ) -> ORHVValidationRecord:
        """Run Phase 2 for a candidate setup and persist the validation record."""
        symbol = setup.symbol
        candidate_dt = date_to_utc_midnight(candidate_date)

        # Load prior candidates (strictly before candidate_date)
        prior_setups = await self._setup_repo.get_candidates_before_date(
            symbol=symbol,
            before_date=candidate_dt,
            limit=self._cfg.lookback_occurrences + 50,
        )
        prior_dates = sorted(
            [s.setup_date.date().isoformat() for s in prior_setups]
        )

        # Load candle data for execution dates of each prior setup
        # We need Day D+1 candles for each prior setup date
        candle_history = await self._load_candle_history_for_validation(
            symbol=symbol,
            setup_dates=[s.setup_date for s in prior_setups],
        )

        # Run Phase 2
        outcome = self._validator.validate(
            symbol=symbol,
            candidate_date=candidate_date,
            prior_setup_dates=prior_dates,
            candle_history=candle_history,
        )

        # Build validation record
        doc = ORHVValidationRecord(
            symbol=symbol,
            candidate_date=candidate_dt,
            execution_date=execution_dt,
            occurrences_available=outcome.occurrences_available,
            occurrences_used=outcome.occurrences_used,
            wins=outcome.wins,
            losses=outcome.losses,
            win_rate=outcome.win_rate,
            avg_pnl=outcome.avg_pnl,
            total_pnl=outcome.total_pnl,
            tradable=outcome.tradable,
            rejection_reason=outcome.rejection_reason,
            simulated_trades=[
                {
                    "setup_date": t.setup_date_str,
                    "execution_date": t.execution_date_str,
                    "trade_side": t.trade_side,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "stop_loss": t.stop_loss,
                    "pnl": t.pnl,
                    "exit_reason": t.exit_reason,
                    "is_win": t.is_win,
                    "orb_range_pct": t.orb_range_pct,
                }
                for t in outcome.trade_outcomes
            ],
        )
        await self._val_repo.upsert(doc)

        # Update rolling statistics
        await self._update_statistics(symbol, doc)

        return doc

    async def _load_candle_history_for_validation(
        self,
        symbol: str,
        setup_dates: list[datetime],
    ) -> dict[str, list]:
        """
        Load Day D+1 candle data for each setup date.

        Returns dict: date_str → sorted list of CandleData objects.
        """
        from datetime import timedelta as td

        history: dict[str, list] = {}
        for setup_dt in setup_dates:
            # We need the day AFTER the setup date
            next_dt = setup_dt + td(days=1)
            buckets = await self._candle_repo.get_candles_between_dates(
                symbol=symbol,
                interval=str(CandleInterval.FIFTEEN_MINUTE),
                from_date=next_dt,
                to_date=next_dt + td(days=1),
            )
            for b in buckets:
                d_str = utc_midnight_to_date(b.trading_date).isoformat()
                if d_str not in history:
                    history[d_str] = []
                history[d_str].extend(b.candles)

        # Sort each day's candles
        for d_str in history:
            history[d_str].sort(key=lambda c: c.time)

        return history

    async def _update_statistics(
        self, symbol: str, latest_validation: ORHVValidationRecord
    ) -> None:
        """Recompute and persist ORHVStatistics for a symbol."""
        total_setups = await self._setup_repo.document_model.find(
            ORHVSetup.symbol == symbol.upper(),
            ORHVSetup.is_candidate == True,
        ).count()

        tradable_count = await self._val_repo.document_model.find(
            ORHVValidationRecord.symbol == symbol.upper(),
            ORHVValidationRecord.tradable == True,
        ).count()

        # Average win rate across all validations
        all_val = await self._val_repo.get_recent_for_symbol(symbol, limit=100)
        avg_wr = (
            sum(v.win_rate for v in all_val) / len(all_val) if all_val else 0.0
        )

        existing = await self._stats_repo.get_by_symbol(symbol)
        stat = existing or ORHVStatistics(symbol=symbol.upper())
        stat.total_setups_detected = total_setups
        stat.tradable_setups = tradable_count
        stat.tradable_rate = tradable_count / total_setups if total_setups > 0 else 0.0
        stat.current_win_rate = latest_validation.win_rate
        stat.avg_historical_win_rate = round(avg_wr, 6)
        stat.last_setup_date = latest_validation.candidate_date

        await self._stats_repo.upsert(stat)


# ── ORHV Run Manager ──────────────────────────────────────────────────────────


@dataclass
class ORHVRunSnapshot:
    running: bool
    last_status: str
    last_started_at: Optional[datetime] = None
    last_finished_at: Optional[datetime] = None
    last_target_date: Optional[date] = None
    last_total_checked: int = 0
    last_total_shortlisted: int = 0
    last_duration_seconds: Optional[float] = None
    last_error: Optional[str] = None
    last_trigger: Optional[str] = None

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
                round(self.last_duration_seconds, 3)
                if self.last_duration_seconds is not None
                else None
            ),
            "last_error": self.last_error,
            "last_trigger": self.last_trigger,
        }


class ORHVRunManager:
    """Single-flight runner for ORHV shortlist pipeline (manual + scheduler)."""

    # Runs longer than this without finishing are treated as stale (e.g. server
    # reload). A full-universe pipeline with a 365-day history backfill must
    # detect ~246 historical days × ~500 stocks (~0.55s/symbol ≈ 19h of
    # detection on top of the candle sync), so this is sized for a long
    # overnight run. NOTE: this is still an in-memory asyncio task — any server
    # restart/reload cancels it mid-run with no resume.
    _STALE_RUN_SECONDS = 24 * 60 * 60

    def __init__(self, service: Optional[ORHVService] = None) -> None:
        self._service = service or ORHVService()
        self._lock = asyncio.Lock()
        self._state = ORHVRunSnapshot(running=False, last_status="idle")
        self._background_task: Optional[asyncio.Task] = None

    @property
    def is_running(self) -> bool:
        self._reset_if_stale()
        return self._state.running

    def snapshot(self) -> ORHVRunSnapshot:
        self._reset_if_stale()
        return self._state

    def _reset_if_stale(self) -> None:
        if not self._state.running or not self._state.last_started_at:
            return
        age = (datetime.now(timezone.utc) - self._state.last_started_at).total_seconds()
        if age < self._STALE_RUN_SECONDS:
            return
        logger.warning(
            "ORHV run stale after %.0fs — resetting run manager (started %s).",
            age,
            self._state.last_started_at.isoformat(),
        )
        self._state = ORHVRunSnapshot(
            running=False,
            last_status="error",
            last_started_at=self._state.last_started_at,
            last_finished_at=datetime.now(timezone.utc),
            last_target_date=self._state.last_target_date,
            last_error="Run timed out or was interrupted (stale state cleared).",
            last_trigger=self._state.last_trigger,
        )
        if self._background_task and not self._background_task.done():
            self._background_task.cancel()

    async def start_background(
        self,
        target_date: Optional[date] = None,
        win_rate_threshold: Optional[float] = None,
        trigger: str = "manual",
        full_pipeline: bool = True,
    ) -> None:
        """Start the pipeline in a background task (for long manual UI runs)."""
        self._reset_if_stale()
        if self._state.running:
            raise ConflictException(
                "An ORHV shortlist run is already in progress.",
                detail={
                    "started_at": (
                        self._state.last_started_at.isoformat()
                        if self._state.last_started_at
                        else None
                    ),
                    "trigger": self._state.last_trigger,
                },
            )

        self._state = ORHVRunSnapshot(
            running=True,
            last_status="running",
            last_started_at=datetime.now(timezone.utc),
            last_target_date=target_date,
            last_trigger=trigger,
        )
        self._background_task = asyncio.create_task(
            self._execute(
                target_date=target_date,
                win_rate_threshold=win_rate_threshold,
                trigger=trigger,
                full_pipeline=full_pipeline,
            )
        )

    async def run(
        self,
        target_date: Optional[date] = None,
        win_rate_threshold: Optional[float] = None,
        trigger: str = "manual",
        full_pipeline: bool = True,
    ) -> ORHVShortlistResult:
        """Blocking run — used by the scheduler."""
        self._reset_if_stale()
        if self._state.running:
            raise ConflictException(
                "An ORHV shortlist run is already in progress.",
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
            if self._state.running:
                raise ConflictException("An ORHV shortlist run is already in progress.")

            self._state = ORHVRunSnapshot(
                running=True,
                last_status="running",
                last_started_at=datetime.now(timezone.utc),
                last_target_date=target_date,
                last_trigger=trigger,
            )
            return await self._execute(
                target_date=target_date,
                win_rate_threshold=win_rate_threshold,
                trigger=trigger,
                full_pipeline=full_pipeline,
            )

    async def _execute(
        self,
        target_date: Optional[date],
        win_rate_threshold: Optional[float],
        trigger: str,
        full_pipeline: bool,
    ) -> ORHVShortlistResult:
        try:
            if full_pipeline:
                result = await self._service.run_full_pipeline(
                    target_date=target_date,
                    win_rate_threshold=win_rate_threshold,
                )
            else:
                result = await self._service.generate_shortlist(
                    target_date=target_date,
                    win_rate_threshold=win_rate_threshold,
                )
            self._state = ORHVRunSnapshot(
                running=False,
                last_status="success",
                last_started_at=self._state.last_started_at,
                last_finished_at=datetime.now(timezone.utc),
                last_target_date=result.execution_date,
                last_total_checked=result.total_candidates_checked,
                last_total_shortlisted=sum(1 for e in result.entries if e.tradable),
                last_duration_seconds=result.duration_seconds,
                last_trigger=trigger,
            )
            return result
        except Exception as exc:
            self._state = ORHVRunSnapshot(
                running=False,
                last_status="error",
                last_started_at=self._state.last_started_at,
                last_finished_at=datetime.now(timezone.utc),
                last_target_date=self._state.last_target_date,
                last_error=str(exc),
                last_trigger=trigger,
            )
            logger.exception("ORHV shortlist run failed: %s", exc)
            raise


orhv_run_manager = ORHVRunManager()
