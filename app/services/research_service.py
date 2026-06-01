"""
Research service — orchestrates the full research and optimization workflow.

Responsibilities:
  1. Validate ResearchConfig and create a ResearchRun document.
  2. Resolve the symbol list (same logic as BacktestService).
  3. Pre-fetch ALL required data ONCE into memory — OSD history, candles,
     continuation probabilities (never reload per sweep point).
  4. Run ParameterOptimizer in a thread-pool executor (CPU-bound sweep).
  5. Load trade records from a reference backtest run for analytics.
  6. Run all analytics engines (Stock, Time, MarketCondition, Failure).
  7. Run ReportGenerator to build the full research report.
  8. Persist all results (optimization docs, stock analytics, run metadata).
  9. Finalize the ResearchRun with COMPLETED status.

Architecture constraints enforced:
  - Calls repositories only — never Beanie/Motor directly.
  - BacktestEngine, ParameterOptimizer, all analytics engines are pure Python.
  - No broker imports — fully broker-independent.
  - All DB access is async; CPU-bound work offloaded to thread-pool.
  - Pre-fetched data dicts are passed by reference — no copies made.
"""

import asyncio
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from app.config.settings import settings
from app.core.exceptions import (
    BacktestNotFoundException,
    ResearchConfigException,
    ResearchException,
    ResearchNotFoundException,
)
from app.models.parameter_optimization_result import ParameterOptimizationResult
from app.models.research_run import ResearchRun, ResearchRunStatus
from app.models.stock_performance_analytics import StockPerformanceAnalytics
from app.repositories.backtest_run_repository import BacktestRunRepository
from app.repositories.backtest_trade_repository import BacktestTradeRepository
from app.repositories.continuation_statistic_repository import ContinuationStatisticRepository
from app.repositories.historical_candle_repository import HistoricalCandleRepository
from app.repositories.one_side_day_repository import OneSideDayRepository
from app.repositories.parameter_optimization_repository import ParameterOptimizationRepository
from app.repositories.research_run_repository import ResearchRunRepository
from app.repositories.stock_performance_analytics_repository import StockPerformanceAnalyticsRepository
from app.repositories.stock_repository import StockRepository
from app.research.failure_analytics import FailureAnalyticsEngine
from app.research.market_condition_analytics import MarketConditionAnalyticsEngine
from app.research.parameter_optimizer import ParameterOptimizer, ResearchConfig, SweepResult
from app.research.report_generator import ReportGenerator, ResearchReport
from app.research.stock_analytics import StockAnalyticsEngine, StockAnalyticsResult
from app.research.time_analytics import TimeAnalyticsEngine
from app.services.stock_universe_service import StockUniverseService
from app.utils.candle_intervals import CandleInterval
from app.utils.logger import get_logger
from app.utils.market_time import date_to_utc_midnight, utc_midnight_to_date
from app.utils.trading_day import last_completed_trading_day

logger = get_logger(__name__)


class ResearchService:
    """
    Orchestrates the complete research workflow from configuration to persisted report.

    Typical usage:
        svc = ResearchService()
        run = await svc.run_research(config)            # full async run
        report = await svc.get_report(run.run_id)       # retrieve stored report
    """

    def __init__(self) -> None:
        self._run_repo         = ResearchRunRepository()
        self._opt_repo         = ParameterOptimizationRepository()
        self._spa_repo         = StockPerformanceAnalyticsRepository()
        self._backtest_repo    = BacktestRunRepository()
        self._trade_repo       = BacktestTradeRepository()
        self._osd_repo         = OneSideDayRepository()
        self._cont_repo        = ContinuationStatisticRepository()
        self._candle_repo      = HistoricalCandleRepository()
        self._stock_repo       = StockRepository()
        self._universe_svc     = StockUniverseService()

    # ── Public API ────────────────────────────────────────────────────────────

    async def run_research(self, config: ResearchConfig) -> ResearchRun:
        """
        Execute a complete research run: optimization + all analytics + report.

        Creates a ResearchRun, runs all phases, persists results, and returns
        the COMPLETED (or FAILED) ResearchRun document.
        """
        self._validate_config(config)

        symbols = await self._resolve_symbols(config.symbols)
        if not symbols:
            raise ResearchConfigException("No active symbols found for research.")

        run = ResearchRun(configuration=config.to_dict())
        run = await self._run_repo.create_run(run)
        logger.info(
            "ResearchRun created: run_id=%s | %d symbols | %s → %s",
            run.run_id, len(symbols), config.from_date, config.to_date,
        )

        try:
            run.mark_running()
            await self._run_repo.update_run(run)

            t_start = time.monotonic()

            # ── Phase 1: Pre-fetch data (done ONCE for all sweep runs) ─────────
            logger.info("[%s] Pre-fetching shared historical data ...", run.run_id)
            prob_scores, osd_history, candle_history = await self._prefetch_data(
                symbols=symbols, config=config
            )

            # ── Phase 2: Parameter sweep (CPU-bound → thread-pool) ─────────────
            logger.info("[%s] Launching ParameterOptimizer ...", run.run_id)
            optimizer = ParameterOptimizer(config)
            loop = asyncio.get_event_loop()
            sweep_result: SweepResult = await loop.run_in_executor(
                None,
                optimizer.run_sweep,
                run.run_id,
                symbols,
                prob_scores,
                osd_history,
                candle_history,
            )

            # Persist optimization results
            await self._save_optimization_results(run.run_id, sweep_result)

            # ── Phase 3: Analytics on trade data ──────────────────────────────
            # Load trade records from the most recent completed backtest run
            # that overlaps this research config's date range.
            trades = await self._load_reference_trades(config)

            stock_result = None
            time_result = None
            market_result = None
            failure_result = None
            spa_docs: list[StockPerformanceAnalytics] = []

            if trades:
                logger.info(
                    "[%s] Running analytics on %d reference trades ...",
                    run.run_id, len(trades),
                )

                # Run analytics engines in thread-pool (CPU-bound)
                stock_engine  = StockAnalyticsEngine()
                time_engine   = TimeAnalyticsEngine()
                market_engine = MarketConditionAnalyticsEngine()
                failure_engine = FailureAnalyticsEngine()

                stock_result, time_result, market_result, failure_result = (
                    await loop.run_in_executor(
                        None,
                        self._run_analytics_engines,
                        stock_engine,
                        time_engine,
                        market_engine,
                        failure_engine,
                        trades,
                    )
                )

                # Convert StockAnalytics to DB documents
                spa_docs = self._build_spa_documents(
                    run_id=run.run_id,
                    stock_result=stock_result,
                )
                await self._spa_repo.upsert_bulk(spa_docs)
            else:
                logger.warning(
                    "[%s] No reference trade data found — analytics sections will be empty.",
                    run.run_id,
                )

            # ── Phase 4: Report generation ─────────────────────────────────────
            generator = ReportGenerator()
            report: ResearchReport = generator.generate(
                run_id=run.run_id,
                sweep_result=sweep_result,
                stock_result=stock_result,
                time_result=time_result,
                market_result=market_result,
                failure_result=failure_result,
            )

            elapsed = round(time.monotonic() - t_start, 1)

            summary_metadata = {
                "report": report.to_dict(),
                "elapsed_seconds": elapsed,
                "sweep_points": len(sweep_result.points),
                "symbols_analysed": len(symbols),
                "reference_trades_used": len(trades),
            }
            run.mark_completed(summary_metadata)
            await self._run_repo.update_run(run)

            logger.info(
                "[%s] ResearchRun COMPLETED in %.1fs: %d sweep points, %d stocks analysed.",
                run.run_id, elapsed, len(sweep_result.points), len(spa_docs),
            )
            return run

        except Exception as exc:
            error_msg = str(exc)
            run.mark_failed(error_msg)
            try:
                await self._run_repo.update_run(run)
            except Exception:
                pass
            logger.error("[%s] ResearchRun FAILED: %s", run.run_id, error_msg, exc_info=True)
            raise ResearchException(f"Research run failed: {error_msg}", detail=error_msg)

    async def get_run(self, run_id: str) -> ResearchRun:
        """Return a ResearchRun by run_id, or raise ResearchNotFoundException."""
        run = await self._run_repo.get_by_run_id(run_id)
        if run is None:
            raise ResearchNotFoundException(run_id)
        return run

    async def list_runs(
        self,
        status: Optional[ResearchRunStatus] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[ResearchRun], int]:
        """Return (runs, total_count) for pagination."""
        skip = (page - 1) * page_size
        runs  = await self._run_repo.list_runs(status=status, limit=page_size, skip=skip)
        total = await self._run_repo.count_runs(status=status)
        return runs, total

    async def get_optimization_results(
        self,
        run_id: str,
        parameter_name: Optional[str] = None,
    ) -> list[ParameterOptimizationResult]:
        """Return all optimization results for a run, optionally filtered by parameter."""
        await self.get_run(run_id)  # raises ResearchNotFoundException if not found
        return await self._opt_repo.get_by_run_id(run_id, parameter_name=parameter_name)

    async def get_stock_analytics(
        self,
        metric: str = "expectancy",
        limit: int = 50,
        min_trades: int = 3,
    ) -> list[StockPerformanceAnalytics]:
        """Return stock analytics leaderboard sorted by the given metric."""
        return await self._spa_repo.get_all_ranked(
            metric=metric, limit=limit, min_trades=min_trades
        )

    async def get_report(self, run_id: str) -> dict:
        """Return the generated research report dict for a completed run."""
        run = await self.get_run(run_id)
        return run.metadata.get("report", {})

    # ── Internal orchestration ─────────────────────────────────────────────────

    @staticmethod
    def _validate_config(config: ResearchConfig) -> None:
        """Raise ResearchConfigException for invalid configurations."""
        if config.from_date > config.to_date:
            raise ResearchConfigException(
                f"from_date {config.from_date} must be ≤ to_date {config.to_date}."
            )
        max_date = last_completed_trading_day()
        if config.to_date > max_date:
            config.to_date = max_date

        if not (0.0 <= config.base_probability_threshold <= 1.0):
            raise ResearchConfigException("base_probability_threshold must be in [0.0, 1.0].")
        if config.capital_per_trade <= 0:
            raise ResearchConfigException("capital_per_trade must be > 0.")

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
        config: ResearchConfig,
    ) -> tuple[dict, dict, dict]:
        """
        Pre-fetch all data required for the full parameter sweep.

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
            "[%s] Data pre-fetched: %d symbols, %d candle-day buckets.",
            "prefetch",
            len(symbols),
            total_candle_buckets,
        )
        return prob_scores, osd_history, candle_history

    async def _load_reference_trades(self, config: ResearchConfig) -> list:
        """
        Load trade records from the most recent COMPLETED backtest run whose
        date range overlaps this research config's range.

        Returns an empty list if no suitable run is found.
        """
        # Find all completed backtest runs
        from app.models.backtest_run import BacktestRunStatus
        runs, _ = await self._backtest_repo.list_runs(
            status=BacktestRunStatus.COMPLETED, limit=20, skip=0
        )
        if not runs:
            logger.info("No completed backtest runs found for reference trades.")
            return []

        # Pick the most recent run whose date range overlaps the research range
        ref_run = None
        research_from = date_to_utc_midnight(config.from_date)
        research_to   = date_to_utc_midnight(config.to_date)

        for run in runs:
            if run.backtest_from is None or run.backtest_to is None:
                continue
            # Overlap check
            if run.backtest_to >= research_from and run.backtest_from <= research_to:
                ref_run = run
                break

        if ref_run is None:
            logger.info("No overlapping completed backtest run found for analytics.")
            return []

        logger.info(
            "Using backtest run %s (%s → %s) as reference for analytics.",
            ref_run.run_id,
            ref_run.backtest_from.date() if ref_run.backtest_from else "?",
            ref_run.backtest_to.date() if ref_run.backtest_to else "?",
        )

        # Load all trades for this run (no pagination — analytics engine handles all)
        trades = await self._trade_repo.get_all_trades_for_run(ref_run.run_id)
        return trades

    @staticmethod
    def _run_analytics_engines(
        stock_engine: StockAnalyticsEngine,
        time_engine: TimeAnalyticsEngine,
        market_engine: MarketConditionAnalyticsEngine,
        failure_engine: FailureAnalyticsEngine,
        trades: list,
    ) -> tuple:
        """
        Run all four analytics engines synchronously.

        This function is designed to be called from run_in_executor()
        so it must be purely synchronous (no async).
        """
        stock_result   = stock_engine.analyse(trades)
        time_result    = time_engine.analyse(trades)
        market_result  = market_engine.analyse(trades)
        failure_result = failure_engine.analyse(trades)
        return stock_result, time_result, market_result, failure_result

    async def _save_optimization_results(
        self,
        run_id: str,
        sweep: SweepResult,
    ) -> None:
        """Convert OptimizationPoint list to Beanie documents and bulk-insert."""
        if not sweep.points:
            return

        docs: list[ParameterOptimizationResult] = []
        for point in sweep.points:
            m = point.metrics
            docs.append(ParameterOptimizationResult(
                run_id=run_id,
                parameter_name=point.parameter_name,
                parameter_value=point.parameter_value,
                configuration=point.config.to_dict(),
                total_trades=m.total_trades,
                winning_trades=m.winning_trades,
                losing_trades=m.losing_trades,
                no_entry_days=m.no_entry_days,
                total_candidate_days=m.total_candidate_days,
                win_rate=m.win_rate,
                sl_hit_rate=m.sl_hit_rate,
                breakout_success_rate=m.breakout_success_rate,
                total_pnl=m.total_pnl,
                avg_pnl_per_trade=m.avg_pnl_per_trade,
                avg_win=m.avg_win,
                avg_loss=m.avg_loss,
                expectancy=m.expectancy,
                profit_factor=m.profit_factor,
                max_drawdown=m.max_drawdown,
                max_drawdown_percent=m.max_drawdown_percent,
                sharpe_ratio=m.sharpe_ratio,
            ))

        count = await self._opt_repo.bulk_insert(docs)
        logger.info("[%s] Saved %d optimization result documents.", run_id, count)

    @staticmethod
    def _build_spa_documents(
        run_id: str,
        stock_result: StockAnalyticsResult,
    ) -> list[StockPerformanceAnalytics]:
        """Convert StockAnalytics dataclasses to StockPerformanceAnalytics Beanie docs."""
        docs: list[StockPerformanceAnalytics] = []
        for a in stock_result.symbol_analytics:
            docs.append(StockPerformanceAnalytics(
                symbol=a.symbol,
                total_trades=a.total_trades,
                winning_trades=a.winning_trades,
                losing_trades=a.losing_trades,
                win_rate=a.win_rate,
                sl_hit_rate=a.sl_hit_rate,
                breakout_success_rate=a.breakout_success_rate,
                total_pnl=a.total_pnl,
                avg_pnl=a.avg_pnl,
                max_win=a.max_win,
                max_loss=a.max_loss,
                expectancy=a.expectancy,
                profit_factor=a.profit_factor,
                max_drawdown=a.max_drawdown,
                avg_orb_range_pct=a.avg_orb_range_pct,
                avg_move_after_breakout_pct=a.avg_move_after_breakout_pct,
                best_breakout_time_range=a.best_breakout_time_range,
                last_run_id=run_id,
            ))
        return docs
