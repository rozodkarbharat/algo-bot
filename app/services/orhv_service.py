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
from app.strategy.strategies.opening_range_historical_validation.constants import STRATEGY_ID
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
from app.utils.trading_day import get_previous_trading_day

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
