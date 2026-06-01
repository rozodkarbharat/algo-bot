"""
Walk-Forward Service — orchestrates the full walk-forward validation workflow.

Responsibilities:
  1. Validate WalkForwardConfig and create a WalkForwardRun document.
  2. Resolve the symbol list (same logic as ResearchService).
  3. Pre-fetch ALL required data ONCE into memory — OSD history, candles,
     continuation probabilities (never reload per window).
  4. Generate walk-forward windows via WalkForwardWindowGenerator.
  5. Run WalkForwardEngine in a thread-pool executor (CPU-bound).
  6. Save SegmentResult records as WalkForwardSegment documents (bulk insert).
  7. Run WalkForwardAggregator and RobustnessAnalyzer on the completed segments.
  8. Finalize the WalkForwardRun with COMPLETED status and summary metadata.

Architecture constraints enforced:
  - Calls repositories only — never Beanie/Motor directly.
  - WalkForwardEngine, WalkForwardAggregator, RobustnessAnalyzer are pure Python.
  - No broker imports — fully broker-independent.
  - All DB access is async; CPU-bound work offloaded to thread-pool.
  - Pre-fetched data dicts are passed by reference — no copies made.
"""

import asyncio
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from app.config.settings import settings  # (may not be needed)
from app.core.exceptions import (
    TradingBotException,
)
from app.models.walk_forward_run import WalkForwardRun, WalkForwardRunStatus
from app.models.walk_forward_segment import WalkForwardSegment
from app.repositories.walk_forward_run_repository import WalkForwardRunRepository
from app.repositories.walk_forward_segment_repository import WalkForwardSegmentRepository
from app.repositories.continuation_statistic_repository import ContinuationStatisticRepository
from app.repositories.historical_candle_repository import HistoricalCandleRepository
from app.repositories.one_side_day_repository import OneSideDayRepository
from app.repositories.stock_repository import StockRepository
from app.research.walk_forward.window_generator import WalkForwardConfig, WalkForwardWindowGenerator
from app.research.walk_forward.engine import WalkForwardEngine, WalkForwardEngineResult
from app.research.walk_forward.aggregator import WalkForwardAggregator
from app.research.walk_forward.robustness_analyzer import RobustnessAnalyzer
from app.services.stock_universe_service import StockUniverseService
from app.utils.candle_intervals import CandleInterval
from app.utils.logger import get_logger
from app.utils.market_time import date_to_utc_midnight, utc_midnight_to_date
from app.utils.trading_day import last_completed_trading_day

logger = get_logger(__name__)


# ── Custom exceptions ─────────────────────────────────────────────────────────

class WalkForwardConfigException(TradingBotException):
    def __init__(self, message: str) -> None:
        super().__init__(message=message, status_code=422)


class WalkForwardNotFoundException(TradingBotException):
    def __init__(self, run_id: str) -> None:
        super().__init__(message=f"Walk-forward run not found: {run_id}", status_code=404)


class WalkForwardException(TradingBotException):
    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message=message, status_code=500, detail=detail)


# ── Service ───────────────────────────────────────────────────────────────────

class WalkForwardService:
    """
    Orchestrates the complete walk-forward validation workflow from
    configuration to persisted results.

    Typical usage:
        svc = WalkForwardService()
        run = await svc.run_walk_forward(config)      # full async run
        result = await svc.get_results(run.run_id)    # retrieve stored results
    """

    def __init__(self) -> None:
        self._run_repo       = WalkForwardRunRepository()
        self._segment_repo   = WalkForwardSegmentRepository()
        self._osd_repo       = OneSideDayRepository()
        self._cont_repo      = ContinuationStatisticRepository()
        self._candle_repo    = HistoricalCandleRepository()
        self._stock_repo     = StockRepository()
        self._universe_svc   = StockUniverseService()

    # ── Public API ────────────────────────────────────────────────────────────

    async def run_walk_forward(self, config: WalkForwardConfig) -> WalkForwardRun:
        """
        Execute a complete walk-forward validation run.

        Creates a WalkForwardRun, runs all phases, persists results, and returns
        the COMPLETED (or FAILED) WalkForwardRun document.
        """
        self._validate_config(config)

        symbols = await self._resolve_symbols(config.symbols)
        if not symbols:
            raise WalkForwardConfigException("No active symbols found for walk-forward run.")

        # Validate enough data for at least 1 window before persisting
        generator = WalkForwardWindowGenerator(config)
        try:
            windows = generator.generate()
        except ValueError as exc:
            raise WalkForwardConfigException(str(exc))

        run = WalkForwardRun(configuration=config.to_dict())
        run = await self._run_repo.create_run(run)
        logger.info(
            "WalkForwardRun created: run_id=%s | %d symbols | %s → %s | %d windows",
            run.run_id, len(symbols), config.from_date, config.to_date, len(windows),
        )

        try:
            run.mark_running()
            await self._run_repo.update_run(run)

            t_start = time.monotonic()

            # ── Phase 1: Pre-fetch data (done ONCE for all windows) ───────────
            logger.info("[%s] Pre-fetching shared historical data ...", run.run_id)
            prob_scores, osd_history, candle_history = await self._prefetch_data(
                symbols=symbols, config=config
            )

            # ── Phase 2: Walk-forward engine (CPU-bound → thread-pool) ────────
            logger.info(
                "[%s] Launching WalkForwardEngine over %d windows ...",
                run.run_id, len(windows),
            )
            engine = WalkForwardEngine(config)
            loop = asyncio.get_event_loop()
            result: WalkForwardEngineResult = await loop.run_in_executor(
                None,
                engine.run,
                run.run_id,
                windows,
                symbols,
                prob_scores,
                osd_history,
                candle_history,
            )

            # ── Phase 3: Persist segment documents ────────────────────────────
            segment_docs = [
                self._build_segment_doc(run.run_id, seg)
                for seg in result.segments
            ]
            if segment_docs:
                await self._segment_repo.bulk_insert(segment_docs)
                logger.info(
                    "[%s] Saved %d segment documents.",
                    run.run_id, len(segment_docs),
                )

            # ── Phase 4: Aggregate + robustness ───────────────────────────────
            aggregated = WalkForwardAggregator().aggregate(result.segments)
            robustness = RobustnessAnalyzer().analyze(result.segments)

            elapsed = round(time.monotonic() - t_start, 1)

            completed_segs = len(result.segments) - result.failed_segments
            summary_metadata = {
                "total_segments": len(result.segments),
                "completed_segments": completed_segs,
                "failed_segments": result.failed_segments,
                "aggregated": aggregated.to_dict(),
                "robustness": robustness.to_dict(),
                "elapsed_seconds": elapsed,
            }
            run.mark_completed(summary_metadata)
            await self._run_repo.update_run(run)

            logger.info(
                "[%s] WalkForwardRun COMPLETED in %.1fs: %d/%d segments succeeded.",
                run.run_id, elapsed, completed_segs, len(result.segments),
            )
            return run

        except Exception as exc:
            error_msg = str(exc)
            run.mark_failed(error_msg)
            try:
                await self._run_repo.update_run(run)
            except Exception:
                pass
            logger.error("[%s] WalkForwardRun FAILED: %s", run.run_id, error_msg, exc_info=True)
            raise WalkForwardException(
                f"Walk-forward run failed: {error_msg}", detail=error_msg
            )

    async def list_runs(
        self,
        status: Optional[WalkForwardRunStatus] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[WalkForwardRun], int]:
        """Return (runs, total_count) for pagination."""
        skip = (page - 1) * page_size
        runs  = await self._run_repo.list_runs(status=status, limit=page_size, skip=skip)
        total = await self._run_repo.count_runs(status=status)
        return runs, total

    async def get_run(self, run_id: str) -> WalkForwardRun:
        """Return a WalkForwardRun by run_id, or raise WalkForwardNotFoundException."""
        run = await self._run_repo.get_by_run_id(run_id)
        if run is None:
            raise WalkForwardNotFoundException(run_id)
        return run

    async def get_results(self, run_id: str) -> dict:
        """
        Return a comprehensive results dict for a completed walk-forward run.

        Fetches the run and all stored segment documents, recomputes aggregate
        and robustness metrics from stored segment metrics, and returns a
        structured dict containing run_data, segments_data, aggregated, and
        robustness.
        """
        run = await self.get_run(run_id)  # raises WalkForwardNotFoundException if missing
        segments = await self._segment_repo.get_segments_for_run(run_id)

        # Recompute from stored segment metrics using the stored metadata when
        # available; otherwise return the raw stored metadata from the run doc.
        aggregated = run.metadata.get("aggregated", {})
        robustness = run.metadata.get("robustness", {})

        segments_data = [
            {
                "segment_id": seg.segment_id,
                "segment_number": seg.segment_number,
                "training_start": seg.training_start.isoformat(),
                "training_end": seg.training_end.isoformat(),
                "testing_start": seg.testing_start.isoformat(),
                "testing_end": seg.testing_end.isoformat(),
                "selected_parameters": seg.selected_parameters,
                "optimization_score": seg.optimization_score,
                "metrics": seg.metrics,
                "status": seg.status,
                "error_message": seg.error_message,
                "created_at": seg.created_at.isoformat(),
            }
            for seg in segments
        ]

        run_data = {
            "run_id": run.run_id,
            "strategy_id": run.strategy_id,
            "strategy_name": run.strategy_name,
            "status": run.status.value,
            "configuration": run.configuration,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "error_message": run.error_message,
            "created_at": run.created_at.isoformat(),
        }

        return {
            "run_data": run_data,
            "segments_data": segments_data,
            "aggregated": aggregated,
            "robustness": robustness,
        }

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _build_segment_doc(self, run_id: str, segment_result) -> WalkForwardSegment:
        """
        Convert a SegmentResult from WalkForwardEngine into a WalkForwardSegment
        Beanie document ready for persistence.
        """
        window = segment_result.window
        m = segment_result.oos_metrics

        training_start_dt = datetime.combine(
            window.training_start, datetime.min.time(), tzinfo=timezone.utc
        )
        training_end_dt = datetime.combine(
            window.training_end, datetime.min.time(), tzinfo=timezone.utc
        )
        testing_start_dt = datetime.combine(
            window.testing_start, datetime.min.time(), tzinfo=timezone.utc
        )
        testing_end_dt = datetime.combine(
            window.testing_end, datetime.min.time(), tzinfo=timezone.utc
        )

        metrics: dict = {}
        if m is not None:
            metrics = {
                "total_trades":    m.total_trades,
                "winning_trades":  m.winning_trades,
                "losing_trades":   m.losing_trades,
                "win_rate":        m.win_rate,
                "total_pnl":       m.total_pnl,
                "avg_pnl_per_trade": m.avg_pnl_per_trade,
                "max_drawdown":    m.max_drawdown,
                "sharpe_ratio":    m.sharpe_ratio,
                "profit_factor":   m.profit_factor,
            }

        status = "failed" if segment_result.error else "completed"

        return WalkForwardSegment(
            segment_id=str(uuid.uuid4()),
            run_id=run_id,
            segment_number=window.segment_number,
            training_start=training_start_dt,
            training_end=training_end_dt,
            testing_start=testing_start_dt,
            testing_end=testing_end_dt,
            selected_parameters=segment_result.selected_parameters,
            optimization_score=segment_result.optimization_sharpe,
            metrics=metrics,
            status=status,
            error_message=segment_result.error,
        )

    @staticmethod
    def _validate_config(config: WalkForwardConfig) -> None:
        """Raise WalkForwardConfigException for invalid configurations."""
        if config.from_date >= config.to_date:
            raise WalkForwardConfigException(
                f"from_date {config.from_date} must be strictly before "
                f"to_date {config.to_date}."
            )
        if config.training_months < 1:
            raise WalkForwardConfigException(
                f"training_months must be >= 1, got {config.training_months}."
            )
        if config.testing_months < 1:
            raise WalkForwardConfigException(
                f"testing_months must be >= 1, got {config.testing_months}."
            )
        if config.step_months < 1:
            raise WalkForwardConfigException(
                f"step_months must be >= 1, got {config.step_months}."
            )
        max_date = last_completed_trading_day()
        if config.from_date > max_date:
            raise WalkForwardConfigException(
                f"from_date {config.from_date} is in the future "
                f"(last completed trading day: {max_date})."
            )
        if config.to_date > max_date:
            config.to_date = max_date

        if config.capital_per_trade <= 0:
            raise WalkForwardConfigException("capital_per_trade must be > 0.")
        if not (0.0 < config.base_probability_threshold <= 1.0):
            raise WalkForwardConfigException(
                "base_probability_threshold must be in (0.0, 1.0]."
            )

    async def _resolve_symbols(self, symbols: Optional[list[str]]) -> list[str]:
        """Return symbol list — passed symbols if provided, else all active stocks."""
        if symbols:
            active = []
            for sym in symbols:
                stock = await self._stock_repo.get_stock_by_symbol(sym.upper())
                if stock and stock.is_active:
                    active.append(sym.upper())
                else:
                    logger.warning("Symbol '%s' not found or inactive — skipping.", sym)
            return active
        stocks = await self._universe_svc.get_active_stocks()
        return [s.symbol for s in stocks]

    async def _prefetch_data(
        self,
        symbols: list[str],
        config: WalkForwardConfig,
    ) -> tuple[dict, dict, dict]:
        """
        Pre-fetch all data required for the full walk-forward run.

        Returns:
            prob_scores:    symbol → continuation_probability
            osd_history:    symbol → date_str → OSD dict
            candle_history: symbol → date_str → list[CandleData]
        """
        # Continuation probabilities
        prob_scores: dict[str, float] = {}
        for sym in symbols:
            stat = await self._cont_repo.get_by_symbol(sym)
            prob_scores[sym] = stat.continuation_probability if stat else 0.0

        # OSD history (7-day lookback before from_date for "yesterday's OSD" logic)
        lookback_start = config.from_date - timedelta(days=7)
        from_dt = date_to_utc_midnight(lookback_start)
        to_dt   = date_to_utc_midnight(config.to_date)

        osd_history: dict[str, dict[str, dict]] = {}
        for sym in symbols:
            records = await self._osd_repo.get_between_dates(
                symbol=sym, from_date=from_dt, to_date=to_dt
            )
            sym_dict: dict[str, dict] = {}
            for rec in records:
                date_str = utc_midnight_to_date(rec.trading_date).isoformat()
                sym_dict[date_str] = {
                    "is_one_side": rec.is_one_side,
                    "direction": rec.direction,
                }
            osd_history[sym] = sym_dict

        # Candle history
        interval = str(CandleInterval.FIFTEEN_MINUTE)
        candle_from_dt = date_to_utc_midnight(config.from_date)
        candle_history: dict[str, dict[str, list]] = {}
        for sym in symbols:
            buckets = await self._candle_repo.get_candles_between_dates(
                symbol=sym, interval=interval,
                from_date=candle_from_dt, to_date=to_dt,
            )
            sym_dict_c: dict[str, list] = {}
            for bucket in buckets:
                date_str = utc_midnight_to_date(bucket.trading_date).isoformat()
                sym_dict_c[date_str] = sorted(bucket.candles, key=lambda c: c.time)
            candle_history[sym] = sym_dict_c

        total_candle_buckets = sum(len(v) for v in candle_history.values())
        logger.info(
            "[walk_forward_prefetch] Data pre-fetched: %d symbols, %d candle-day buckets.",
            len(symbols),
            total_candle_buckets,
        )
        return prob_scores, osd_history, candle_history
