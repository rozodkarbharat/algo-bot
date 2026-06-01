"""
Backtest orchestration service.

Responsibilities:
  1. Validate BacktestConfig and create a BacktestRun document.
  2. Pre-fetch all required data from repositories (OSD history, continuation
     stats, candles) into in-memory lookup dicts.
  3. Run the BacktestEngine in a thread-pool executor (CPU-bound).
  4. Convert SimulatedTrade results to BacktestTrade documents and bulk-insert.
  5. Run the MetricsEngine and persist BacktestMetrics.
  6. Finalize the BacktestRun with summary + status.

Architecture rules enforced:
  - Service calls repositories only — never Beanie/Motor directly.
  - BacktestEngine and MetricsEngine (pure logic) are called here with data.
  - No broker imports — backtesting is completely broker-independent.
  - All DB access is async; CPU-bound engine is offloaded to thread pool.
"""

import asyncio
import time
from datetime import date, datetime, timezone
from typing import Optional

from app.config.settings import settings
from app.core.exceptions import BacktestConfigException, BacktestException, BacktestNotFoundException
from app.models.backtest_run import BacktestRun, BacktestRunStatus
from app.models.backtest_trade import BacktestTrade, ExitReason
from app.repositories.backtest_metrics_repository import BacktestMetricsRepository
from app.repositories.backtest_run_repository import BacktestRunRepository
from app.repositories.backtest_trade_repository import BacktestTradeRepository
from app.repositories.continuation_statistic_repository import ContinuationStatisticRepository
from app.repositories.historical_candle_repository import HistoricalCandleRepository
from app.repositories.one_side_day_repository import OneSideDayRepository
from app.repositories.stock_repository import StockRepository
from app.services.stock_universe_service import StockUniverseService
from app.strategy.backtest_engine import BacktestConfig, BacktestEngine
from app.strategy.metrics_engine import MetricsEngine, MetricsResult
from app.strategy.strategy_registry import registry as strategy_registry
from app.strategy.trade_simulator import SimulatedTrade
from app.utils.candle_intervals import CandleInterval
from app.utils.logger import get_logger
from app.utils.market_time import date_to_utc_midnight, utc_midnight_to_date
from app.utils.trading_day import get_trading_days, last_completed_trading_day

logger = get_logger(__name__)


class BacktestService:
    """
    Orchestrates a full backtest run from configuration to persisted results.

    Typical usage:
        svc = BacktestService()
        run = await svc.start_backtest(config)   # returns immediately
        # ...or...
        run = await svc.run_backtest(config)     # blocks until complete
    """

    def __init__(self) -> None:
        self._run_repo     = BacktestRunRepository()
        self._trade_repo   = BacktestTradeRepository()
        self._metrics_repo = BacktestMetricsRepository()
        self._osd_repo     = OneSideDayRepository()
        self._cont_repo    = ContinuationStatisticRepository()
        self._candle_repo  = HistoricalCandleRepository()
        self._stock_repo   = StockRepository()
        self._universe_svc = StockUniverseService()

    # ── Public API ────────────────────────────────────────────────────────────

    async def run_backtest(
        self,
        config: BacktestConfig,
        strategy_id: str = "one_side_orb",
    ) -> BacktestRun:
        """
        Execute a complete backtest synchronously.

        Creates, runs, and finalizes a BacktestRun document.
        Returns the completed (or FAILED) BacktestRun.

        Args:
            config:      BacktestConfig with date range, symbols, and parameters.
            strategy_id: Which registered strategy to run.  Defaults to
                         'one_side_orb' for backward compatibility.
        """
        self._validate_config(config)

        # Validate strategy exists in registry
        try:
            strategy = strategy_registry.get(strategy_id)
        except KeyError as exc:
            raise BacktestConfigException(str(exc))

        # Resolve symbols
        symbols = await self._resolve_symbols(config.symbols)
        if not symbols:
            raise BacktestConfigException("No active symbols found for the backtest.")

        # Create the run document — store full strategy identity
        run = BacktestRun(
            strategy_id=strategy.strategy_id,
            strategy_name=strategy.strategy_name,
            strategy_version=strategy.strategy_version,
            symbols=symbols,
            backtest_from=date_to_utc_midnight(config.from_date),
            backtest_to=date_to_utc_midnight(config.to_date),
            configuration=config.to_dict(),
        )
        run = await self._run_repo.create_run(run)
        logger.info(
            "BacktestRun created: run_id=%s | strategy=%s | %d symbols | %s → %s",
            run.run_id, strategy_id, len(symbols), config.from_date, config.to_date,
        )

        try:
            run.mark_running()
            await self._run_repo.update_run(run)

            # Pre-fetch all data into memory
            logger.info("[%s] Pre-fetching historical data ...", run.run_id)
            prob_scores, osd_history, candle_history = await self._prefetch_data(
                symbols=symbols,
                config=config,
            )

            # Build the engine via the strategy registry — decouples the service
            # from any specific strategy implementation.
            logger.info("[%s] Launching BacktestEngine via strategy '%s' ...", run.run_id, strategy_id)
            engine = strategy.create_backtest_engine(config.to_dict())
            loop = asyncio.get_event_loop()
            engine_result = await loop.run_in_executor(
                None,
                engine.run,
                symbols,
                prob_scores,
                osd_history,
                candle_history,
            )

            logger.info(
                "[%s] Engine complete: %d trades from %d candidate days.",
                run.run_id,
                len(engine_result.trades),
                engine_result.total_candidate_days,
            )

            # Persist trades (include strategy identity on every trade record)
            await self._save_trades(
                run.run_id,
                engine_result.trades,
                config,
                strategy_id=strategy.strategy_id,
                strategy_name=strategy.strategy_name,
            )

            # Compute metrics (pure Python — no DB dependency)
            metrics_engine = MetricsEngine()
            metrics_result = metrics_engine.calculate(
                run_id=run.run_id,
                trades=engine_result.trades,
                total_candidate_days=engine_result.total_candidate_days,
            )

            # Convert MetricsResult dataclass → BacktestMetrics Beanie document
            metrics_doc = self._metrics_result_to_doc(metrics_result)
            await self._metrics_repo.upsert_metrics(metrics_doc)

            # Build summary and finalize run
            summary = self._build_summary(metrics_result, engine_result)
            run.mark_completed(summary)
            await self._run_repo.update_run(run)

            logger.info(
                "[%s] BacktestRun COMPLETED: %d trades | P&L=₹%.2f | win_rate=%.1f%%",
                run.run_id,
                metrics_result.total_trades,
                metrics_result.total_pnl,
                metrics_result.win_rate * 100,
            )
            return run

        except Exception as exc:
            error_msg = str(exc)
            run.mark_failed(error_msg)
            try:
                await self._run_repo.update_run(run)
            except Exception:
                pass
            logger.error("[%s] BacktestRun FAILED: %s", run.run_id, error_msg, exc_info=True)
            raise BacktestException(f"Backtest failed: {error_msg}", detail=error_msg)

    async def get_run(self, run_id: str) -> BacktestRun:
        """Return a BacktestRun by run_id, or raise BacktestNotFoundException."""
        run = await self._run_repo.get_by_run_id(run_id)
        if run is None:
            raise BacktestNotFoundException(run_id)
        return run

    async def list_runs(
        self,
        strategy_id: Optional[str] = None,
        strategy_name: Optional[str] = None,
        status: Optional[BacktestRunStatus] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[BacktestRun], int]:
        """Return (runs, total_count) for pagination.

        Filter by strategy_id (preferred) or strategy_name (legacy).
        """
        skip = (page - 1) * page_size
        # strategy_id filter takes precedence over strategy_name
        effective_strategy = strategy_id or strategy_name
        runs = await self._run_repo.list_runs(
            strategy_name=effective_strategy, status=status,
            limit=page_size, skip=skip,
        )
        total = await self._run_repo.count_runs(
            strategy_name=effective_strategy, status=status,
        )
        return runs, total

    async def get_metrics(self, run_id: str):
        """Return BacktestMetrics for a run, or raise BacktestNotFoundException."""
        await self.get_run(run_id)  # ensures run exists
        return await self._metrics_repo.get_by_run_id(run_id)

    async def list_trades(
        self,
        run_id: str,
        symbol: Optional[str] = None,
        exit_reason: Optional[ExitReason] = None,
        page: int = 1,
        page_size: int = 100,
    ) -> tuple[list[BacktestTrade], int]:
        """Return (trades, total_count) for a run, with optional filters."""
        await self.get_run(run_id)
        skip = (page - 1) * page_size
        trades = await self._trade_repo.get_trades_for_run(
            run_id=run_id, symbol=symbol, exit_reason=exit_reason,
            limit=page_size, skip=skip,
        )
        total = await self._trade_repo.count_trades_for_run(
            run_id=run_id, symbol=symbol, exit_reason=exit_reason,
        )
        return trades, total

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _validate_config(config: BacktestConfig) -> None:
        """Raise BacktestConfigException for invalid configurations."""
        if config.from_date > config.to_date:
            raise BacktestConfigException(
                f"from_date {config.from_date} must be ≤ to_date {config.to_date}."
            )
        max_date = last_completed_trading_day()
        if config.to_date > max_date:
            config.to_date = max_date
            logger.info("Clamped to_date to last completed trading day: %s", max_date)

        if config.probability_threshold < 0.0 or config.probability_threshold > 1.0:
            raise BacktestConfigException("probability_threshold must be in [0.0, 1.0].")

        if config.capital_per_trade <= 0:
            raise BacktestConfigException("capital_per_trade must be > 0.")

    async def _resolve_symbols(self, symbols: Optional[list[str]]) -> list[str]:
        """Return symbol strings for the given list, or all active NIFTY50 stocks."""
        if symbols:
            active = []
            for sym in symbols:
                stock = await self._stock_repo.get_stock_by_symbol(sym.upper())
                if stock and stock.is_active:
                    active.append(sym.upper())
                else:
                    logger.warning("Symbol '%s' not found or inactive — excluding.", sym)
            return active
        stocks = await self._universe_svc.get_active_stocks()
        return [s.symbol for s in stocks]

    async def _prefetch_data(
        self,
        symbols: list[str],
        config: BacktestConfig,
    ) -> tuple[dict, dict, dict]:
        """
        Pre-fetch all required data into in-memory dicts.

        Returns:
            prob_scores:    symbol → continuation_probability (float)
            osd_history:    symbol → date_str → {is_one_side, direction}
            candle_history: symbol → date_str → list[CandleData]

        OSD history is fetched one day BEFORE from_date so that the very first
        trading day has a valid "yesterday" to look up.
        """
        # 1. Continuation probabilities
        prob_scores = await self._load_prob_scores(symbols)

        # 2. OSD history (need one extra day before from_date)
        osd_history = await self._load_osd_history(symbols, config)

        # 3. Candles for the backtest range
        candle_history = await self._load_candle_history(symbols, config)

        return prob_scores, osd_history, candle_history

    async def _load_prob_scores(self, symbols: list[str]) -> dict[str, float]:
        """Load continuation probabilities for all symbols into a dict."""
        scores: dict[str, float] = {}
        for sym in symbols:
            stat = await self._cont_repo.get_by_symbol(sym)
            if stat is not None:
                scores[sym] = stat.continuation_probability
            else:
                scores[sym] = 0.0
                logger.debug("No continuation stat for %s — defaulting to 0.0", sym)
        logger.info("Loaded probability scores for %d symbols.", len(scores))
        return scores

    async def _load_osd_history(
        self,
        symbols: list[str],
        config: BacktestConfig,
    ) -> dict[str, dict[str, dict]]:
        """
        Load OSD records for [from_date - lookback_buffer, to_date] for all symbols.

        We look back up to 7 calendar days before from_date to ensure we capture
        the most recent trading day before the backtest starts.
        """
        from datetime import timedelta
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

        logger.info(
            "Loaded OSD history: %d symbols, range %s → %s",
            len(osd_history), lookback_start, config.to_date,
        )
        return osd_history

    async def _load_candle_history(
        self,
        symbols: list[str],
        config: BacktestConfig,
    ) -> dict[str, dict[str, list]]:
        """
        Load all 15-min candles for [from_date, to_date] for all symbols.

        Organises into: symbol → date_str → sorted list[CandleData].
        """
        from_dt = date_to_utc_midnight(config.from_date)
        to_dt   = date_to_utc_midnight(config.to_date)
        interval = str(CandleInterval.FIFTEEN_MINUTE)

        candle_history: dict[str, dict[str, list]] = {}

        for sym in symbols:
            buckets = await self._candle_repo.get_candles_between_dates(
                symbol=sym, interval=interval, from_date=from_dt, to_date=to_dt
            )
            sym_dict: dict[str, list] = {}
            for bucket in buckets:
                date_str = utc_midnight_to_date(bucket.trading_date).isoformat()
                candles = sorted(bucket.candles, key=lambda c: c.time)
                sym_dict[date_str] = candles
            candle_history[sym] = sym_dict

        total_buckets = sum(len(v) for v in candle_history.values())
        logger.info(
            "Loaded candle history: %d symbols, %d day-buckets total.",
            len(candle_history), total_buckets,
        )
        return candle_history

    async def _save_trades(
        self,
        run_id: str,
        sim_trades: list[SimulatedTrade],
        config: BacktestConfig,
        strategy_id: str = "one_side_orb",
        strategy_name: str = "One-Side ORB",
    ) -> None:
        """
        Convert SimulatedTrade results to BacktestTrade documents and bulk-insert.

        Writes in batches of settings.BACKTEST_BATCH_SIZE to limit memory footprint.
        """
        if not sim_trades:
            return

        batch_size = settings.BACKTEST_BATCH_SIZE
        batch: list[BacktestTrade] = []
        total_written = 0

        # We need the trading date: derive from entry_time or use a date lookup
        # Pre-build a mapping from symbol for which day we are on.
        # Simulated trades don't carry trading_date explicitly — we infer it from
        # entry_time (IST date) or fall back to the no-breakout metadata.

        import pytz
        IST = pytz.timezone("Asia/Kolkata")

        for sim in sim_trades:
            # Determine trading_date from entry_time or from backtest range context
            if sim.entry_time is not None:
                trade_date = sim.entry_time.astimezone(IST).date()
            elif sim.metadata.get("no_entry_date"):
                trade_date = sim.metadata["no_entry_date"]
            else:
                # For NO_BREAKOUT trades the entry_time is None; we need to
                # pass the date from the engine. The engine doesn't currently
                # embed the date in SimulatedTrade metadata, so we reconstruct
                # it from the trade ordering (trades are appended day-by-day).
                # As a safe fallback, skip the trading_date field population.
                trade_date = config.from_date

            trading_dt = date_to_utc_midnight(trade_date)

            doc = BacktestTrade(
                run_id=run_id,
                symbol=sim.symbol,
                trading_date=trading_dt,
                strategy_id=strategy_id,
                strategy_name=strategy_name,
                trade_side=sim.trade_side,
                breakout_side=sim.breakout_side,
                orb_high=sim.orb_high,
                orb_low=sim.orb_low,
                probability_score=sim.probability_score,
                entry_time=sim.entry_time,
                entry_price=sim.entry_price,
                stop_loss=sim.stop_loss,
                exit_time=sim.exit_time,
                exit_price=sim.exit_price,
                exit_reason=sim.exit_reason,
                quantity=sim.quantity,
                capital_used=sim.capital_used,
                pnl=sim.pnl,
                pnl_percent=sim.pnl_percent,
                risk_reward=sim.risk_reward,
                metadata=sim.metadata,
            )
            batch.append(doc)

            if len(batch) >= batch_size:
                written = await self._trade_repo.bulk_insert_trades(batch)
                total_written += written
                logger.debug("[%s] Inserted batch of %d trades.", run_id, written)
                batch = []

        if batch:
            written = await self._trade_repo.bulk_insert_trades(batch)
            total_written += written

        logger.info("[%s] Total trades persisted: %d", run_id, total_written)

    @staticmethod
    def _metrics_result_to_doc(result: MetricsResult):
        """Convert a MetricsResult dataclass to a BacktestMetrics Beanie document."""
        from app.models.backtest_metrics import BacktestMetrics
        return BacktestMetrics(
            run_id=result.run_id,
            total_trades=result.total_trades,
            winning_trades=result.winning_trades,
            losing_trades=result.losing_trades,
            no_entry_days=result.no_entry_days,
            total_candidate_days=result.total_candidate_days,
            win_rate=result.win_rate,
            sl_hit_rate=result.sl_hit_rate,
            breakout_success_rate=result.breakout_success_rate,
            total_pnl=result.total_pnl,
            avg_pnl_per_trade=result.avg_pnl_per_trade,
            avg_win=result.avg_win,
            avg_loss=result.avg_loss,
            max_win=result.max_win,
            max_loss=result.max_loss,
            max_drawdown=result.max_drawdown,
            max_drawdown_percent=result.max_drawdown_percent,
            profit_factor=result.profit_factor,
            expectancy=result.expectancy,
            sharpe_ratio=result.sharpe_ratio,
            avg_risk_reward=result.avg_risk_reward,
            max_consecutive_wins=result.max_consecutive_wins,
            max_consecutive_losses=result.max_consecutive_losses,
            per_symbol_metrics=result.per_symbol_metrics,
            daily_pnl=result.daily_pnl,
            monthly_pnl=result.monthly_pnl,
        )

    @staticmethod
    def _build_summary(result: MetricsResult, engine_result) -> dict:
        """Build a compact summary dict for BacktestRun.summary_metrics."""
        return {
            "total_trades": result.total_trades,
            "win_rate": result.win_rate,
            "total_pnl": result.total_pnl,
            "profit_factor": result.profit_factor,
            "max_drawdown": result.max_drawdown,
            "sharpe_ratio": result.sharpe_ratio,
            "expectancy": result.expectancy,
            "breakout_success_rate": result.breakout_success_rate,
            "total_candidate_days": engine_result.total_candidate_days,
            "trading_days_processed": engine_result.trading_days_processed,
        }
