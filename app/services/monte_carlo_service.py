"""
Monte Carlo Service — orchestrates the full Monte Carlo risk analysis workflow.

Responsibilities:
  1. Validate configuration and create a MonteCarloRun document.
  2. Fetch historical BacktestTrade P&Ls for each requested strategy.
  3. Run MonteCarloSimulator (CPU-bound, offloaded to thread-pool) for:
       a. Each individual strategy.
       b. Combined portfolio (merged trade sequence, shuffled together).
  4. Persist per-strategy MonteCarloResult documents.
  5. Generate all four report types via ReportGenerator.
  6. Finalize the MonteCarloRun with COMPLETED status.

Architecture constraints:
  - Only calls repositories — never Beanie/Motor directly.
  - MonteCarloSimulator, TradeSampler, ReportGenerator are pure Python.
  - CPU-bound simulation offloaded to asyncio thread-pool executor.
  - NO_BREAKOUT trades (pnl == 0, exit_reason == "NO_BREAKOUT") are excluded
    from sampling — they represent candidate days without an actual trade.
"""

import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.core.exceptions import TradingBotException
from app.models.backtest_trade import ExitReason
from app.models.monte_carlo_run import MonteCarloRun, MonteCarloRunStatus
from app.models.monte_carlo_result import MonteCarloResult
from app.repositories.backtest_trade_repository import BacktestTradeRepository
from app.repositories.monte_carlo_run_repository import MonteCarloRunRepository
from app.repositories.monte_carlo_result_repository import MonteCarloResultRepository
from app.risk.monte_carlo.simulator import MonteCarloConfig, MonteCarloSimulator, MonteCarloSummary
from app.risk.monte_carlo.trade_sampler import SamplingMethod
from app.risk.monte_carlo.report_generator import ReportGenerator
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ── Custom exceptions ─────────────────────────────────────────────────────────

class MonteCarloConfigException(TradingBotException):
    def __init__(self, message: str) -> None:
        super().__init__(message=message, status_code=422)


class MonteCarloNotFoundException(TradingBotException):
    def __init__(self, run_id: str) -> None:
        super().__init__(
            message=f"Monte Carlo run not found: {run_id}", status_code=404
        )


class MonteCarloException(TradingBotException):
    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message=message, status_code=500, detail=detail)


# ── Service ───────────────────────────────────────────────────────────────────

class MonteCarloService:
    """
    Orchestrates the complete Monte Carlo risk analysis workflow.

    Usage:
        svc = MonteCarloService()
        run = await svc.run_simulation(request)
        results = await svc.get_results(run.run_id)
        reports = await svc.get_reports(run.run_id)
    """

    def __init__(self) -> None:
        self._run_repo    = MonteCarloRunRepository()
        self._result_repo = MonteCarloResultRepository()
        self._trade_repo  = BacktestTradeRepository()
        self._report_gen  = ReportGenerator()

    # ── Public API ────────────────────────────────────────────────────────────

    async def run_simulation(
        self,
        strategy_ids: list[str],
        simulation_count: int = 1000,
        starting_capital: float = 1_000_000.0,
        sampling_method: str = "bootstrap",
        ruin_thresholds: Optional[list[float]] = None,
        confidence_levels: Optional[list[float]] = None,
        backtest_run_ids: Optional[list[str]] = None,
        seed: Optional[int] = None,
    ) -> MonteCarloRun:
        """
        Execute a full Monte Carlo risk analysis run.

        Returns the finalized (COMPLETED or FAILED) MonteCarloRun.
        """
        ruin_thresholds    = ruin_thresholds    or [0.50, 0.40, 0.30]
        confidence_levels  = confidence_levels  or [0.90, 0.95, 0.99]

        self._validate_config(
            strategy_ids, simulation_count, starting_capital,
            sampling_method, ruin_thresholds, confidence_levels,
        )

        try:
            method = SamplingMethod(sampling_method)
        except ValueError:
            raise MonteCarloConfigException(
                f"Invalid sampling_method '{sampling_method}'. "
                f"Choose from: bootstrap, random_shuffle, replacement."
            )

        config = MonteCarloConfig(
            starting_capital=starting_capital,
            simulation_count=simulation_count,
            sampling_method=method,
            ruin_thresholds=ruin_thresholds,
            confidence_levels=confidence_levels,
            seed=seed,
        )

        run = MonteCarloRun(
            strategy_ids=strategy_ids,
            simulation_count=simulation_count,
            configuration={
                **config.to_dict(),
                "strategy_ids": strategy_ids,
                "backtest_run_ids": backtest_run_ids,
            },
        )
        run = await self._run_repo.create_run(run)
        logger.info(
            "MonteCarloRun created: run_id=%s | strategies=%s | simulations=%d",
            run.run_id, strategy_ids, simulation_count,
        )

        try:
            run.mark_running()
            await self._run_repo.update_run(run)

            t_start = time.monotonic()

            # ── Phase 1: Fetch trades ──────────────────────────────────────
            strategy_pnls: dict[str, list[float]] = {}
            for sid in strategy_ids:
                pnls = await self._fetch_trade_pnls(sid, backtest_run_ids)
                if pnls:
                    strategy_pnls[sid] = pnls
                else:
                    logger.warning(
                        "[%s] No executed trades found for strategy '%s' — skipping.",
                        run.run_id, sid,
                    )

            if not strategy_pnls:
                raise MonteCarloConfigException(
                    "No executed trade records found for any of the requested strategies. "
                    "Run a backtest first to generate trade history."
                )

            # ── Phase 2: Simulate (CPU-bound → thread-pool) ───────────────
            simulator = MonteCarloSimulator(config)
            loop = asyncio.get_event_loop()

            result_docs: list[MonteCarloResult] = []
            per_strategy_summaries: dict[str, MonteCarloSummary] = {}

            for sid, pnls in strategy_pnls.items():
                logger.info(
                    "[%s] Simulating strategy '%s' with %d trades ...",
                    run.run_id, sid, len(pnls),
                )
                summary: MonteCarloSummary = await loop.run_in_executor(
                    None, simulator.run, pnls
                )
                per_strategy_summaries[sid] = summary
                result_docs.append(
                    self._build_result_doc(run.run_id, sid, summary, starting_capital)
                )

            # ── Phase 3: Combined portfolio simulation ────────────────────
            # Merge all strategy trade P&Ls into a single interleaved sequence,
            # then simulate — captures cross-strategy diversification.
            all_pnls: list[float] = []
            for pnls in strategy_pnls.values():
                all_pnls.extend(pnls)
            # Sort to mix strategies (simple interleave; order doesn't affect bootstrap)
            import random as _random
            rng = _random.Random(seed)
            rng.shuffle(all_pnls)

            portfolio_summary: MonteCarloSummary = await loop.run_in_executor(
                None, simulator.run, all_pnls
            )
            result_docs.append(
                self._build_result_doc(
                    run.run_id, "portfolio", portfolio_summary, starting_capital
                )
            )

            # ── Phase 4: Persist results ───────────────────────────────────
            await self._result_repo.bulk_insert(result_docs)
            logger.info(
                "[%s] Persisted %d result documents.", run.run_id, len(result_docs)
            )

            elapsed = round(time.monotonic() - t_start, 1)
            run.mark_completed(
                {
                    "strategy_count": len(strategy_pnls),
                    "total_trade_count": sum(len(p) for p in strategy_pnls.values()),
                    "elapsed_seconds": elapsed,
                }
            )
            await self._run_repo.update_run(run)

            logger.info(
                "[%s] MonteCarloRun COMPLETED in %.1fs.",
                run.run_id, elapsed,
            )
            return run

        except MonteCarloConfigException:
            raise
        except Exception as exc:
            error_msg = str(exc)
            run.mark_failed(error_msg)
            try:
                await self._run_repo.update_run(run)
            except Exception:
                pass
            logger.error("[%s] MonteCarloRun FAILED: %s", run.run_id, error_msg, exc_info=True)
            raise MonteCarloException(
                f"Monte Carlo run failed: {error_msg}", detail=error_msg
            )

    async def get_run(self, run_id: str) -> MonteCarloRun:
        run = await self._run_repo.get_by_run_id(run_id)
        if run is None:
            raise MonteCarloNotFoundException(run_id)
        return run

    async def list_runs(
        self,
        status: Optional[MonteCarloRunStatus] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[MonteCarloRun], int]:
        skip = (page - 1) * page_size
        runs  = await self._run_repo.list_runs(status=status, limit=page_size, skip=skip)
        total = await self._run_repo.count_runs(status=status)
        return runs, total

    async def get_results(self, run_id: str) -> dict:
        """Return run + all per-strategy result documents."""
        run     = await self.get_run(run_id)
        results = await self._result_repo.get_results_for_run(run_id)

        run_data = {
            "run_id":           run.run_id,
            "strategy_ids":     run.strategy_ids,
            "simulation_count": run.simulation_count,
            "status":           run.status.value,
            "started_at":       run.started_at.isoformat() if run.started_at else None,
            "completed_at":     run.completed_at.isoformat() if run.completed_at else None,
            "configuration":    run.configuration,
            "error_message":    run.error_message,
            "created_at":       run.created_at.isoformat(),
            "metadata":         run.metadata,
        }

        results_data = [self._result_to_dict(r) for r in results]

        return {"run": run_data, "results": results_data}

    async def get_reports(self, run_id: str) -> dict:
        """
        Generate all four report types from stored result documents.

        Reports are produced by ReportGenerator — no simulation re-run needed.
        """
        run     = await self.get_run(run_id)
        results = await self._result_repo.get_results_for_run(run_id)

        if not results:
            raise MonteCarloNotFoundException(run_id)

        starting_capital = run.configuration.get("starting_capital", 1_000_000.0)

        # Separate individual strategy results from the portfolio result
        strategy_results: dict[str, MonteCarloResult] = {}
        portfolio_result: Optional[MonteCarloResult]  = None
        for r in results:
            if r.strategy_id == "portfolio":
                portfolio_result = r
            else:
                strategy_results[r.strategy_id] = r

        # Build MonteCarloSummary objects from stored result documents
        strategy_summaries: dict[str, MonteCarloSummary] = {
            sid: self._result_to_summary(res)
            for sid, res in strategy_results.items()
        }

        risk_reports:     dict = {}
        drawdown_reports: dict = {}
        capital_reports:  dict = {}

        for sid, summary in strategy_summaries.items():
            label = sid
            risk_reports[sid]     = self._report_gen.generate_risk_report(
                summary, label, starting_capital
            )
            drawdown_reports[sid] = self._report_gen.generate_drawdown_report(
                summary, label, starting_capital
            )
            capital_reports[sid]  = self._report_gen.generate_capital_requirement_report(
                summary, label, starting_capital
            )

        # Portfolio reports
        if portfolio_result:
            port_summary = self._result_to_summary(portfolio_result)
            risk_reports["portfolio"]     = self._report_gen.generate_risk_report(
                port_summary, "Combined Portfolio", starting_capital
            )
            drawdown_reports["portfolio"] = self._report_gen.generate_drawdown_report(
                port_summary, "Combined Portfolio", starting_capital
            )
            capital_reports["portfolio"]  = self._report_gen.generate_capital_requirement_report(
                port_summary, "Combined Portfolio", starting_capital
            )
            comparison_report = self._report_gen.generate_strategy_comparison_report(
                strategy_summaries, port_summary, starting_capital
            )
        else:
            # Portfolio result missing — produce minimal comparison report
            comparison_report = {
                "report_type": "strategy_comparison_report",
                "note": "Portfolio result not available.",
            }

        return {
            "run_id":            run_id,
            "risk_reports":      risk_reports,
            "drawdown_reports":  drawdown_reports,
            "capital_reports":   capital_reports,
            "comparison_report": comparison_report,
            "generated_at":      datetime.now(timezone.utc).isoformat(),
        }

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _fetch_trade_pnls(
        self,
        strategy_id: str,
        backtest_run_ids: Optional[list[str]],
    ) -> list[float]:
        """
        Return a list of P&L values for executed trades (NO_BREAKOUT excluded).

        If backtest_run_ids is supplied, only trades from those specific runs
        are used.  Otherwise ALL executed trades for the strategy across every
        backtest run are included — this gives the largest possible sample for
        Monte Carlo.
        """
        pnls: list[float] = []

        if backtest_run_ids:
            for run_id in backtest_run_ids:
                trades = await self._trade_repo.get_all_trades_for_run(run_id)
                for trade in trades:
                    if trade.exit_reason != ExitReason.NO_BREAKOUT:
                        pnls.append(trade.pnl)
        else:
            trades = await self._trade_repo.get_executed_trades_by_strategy(strategy_id)
            pnls = [t.pnl for t in trades]

        return pnls

    @staticmethod
    def _build_result_doc(
        run_id: str,
        strategy_id: str,
        summary: MonteCarloSummary,
        starting_capital: float,
    ) -> MonteCarloResult:
        return MonteCarloResult(
            run_id=run_id,
            strategy_id=strategy_id,
            avg_return=summary.avg_return,
            median_return=summary.median_return,
            best_return=summary.best_return,
            worst_return=summary.worst_return,
            std_return=summary.std_return,
            avg_drawdown=summary.avg_drawdown,
            max_drawdown=summary.max_drawdown,
            probability_of_ruin=summary.probability_of_ruin,
            avg_consecutive_losses=summary.avg_consecutive_losses,
            max_consecutive_losses=summary.max_consecutive_losses,
            return_percentiles=summary.return_percentiles,
            drawdown_percentiles=summary.drawdown_percentiles,
            streak_confidence_intervals=summary.streak_confidence_intervals,
            capital_requirements=summary.capital_requirements,
            trade_count=summary.trade_count,
            simulation_count=summary.simulation_count,
            starting_capital=starting_capital,
        )

    @staticmethod
    def _result_to_dict(r: MonteCarloResult) -> dict:
        return {
            "result_id":                  r.result_id,
            "run_id":                     r.run_id,
            "strategy_id":                r.strategy_id,
            "avg_return":                 r.avg_return,
            "median_return":              r.median_return,
            "best_return":                r.best_return,
            "worst_return":               r.worst_return,
            "std_return":                 r.std_return,
            "avg_drawdown":               r.avg_drawdown,
            "max_drawdown":               r.max_drawdown,
            "probability_of_ruin":        r.probability_of_ruin,
            "avg_consecutive_losses":     r.avg_consecutive_losses,
            "max_consecutive_losses":     r.max_consecutive_losses,
            "return_percentiles":         r.return_percentiles,
            "drawdown_percentiles":       r.drawdown_percentiles,
            "streak_confidence_intervals": r.streak_confidence_intervals,
            "capital_requirements":       r.capital_requirements,
            "trade_count":                r.trade_count,
            "simulation_count":           r.simulation_count,
            "starting_capital":           r.starting_capital,
            "created_at":                 r.created_at.isoformat(),
        }

    @staticmethod
    def _result_to_summary(r: MonteCarloResult) -> MonteCarloSummary:
        """Reconstruct a MonteCarloSummary from a stored MonteCarloResult document."""
        return MonteCarloSummary(
            simulation_count=r.simulation_count,
            trade_count=r.trade_count,
            avg_return=r.avg_return,
            median_return=r.median_return,
            best_return=r.best_return,
            worst_return=r.worst_return,
            std_return=r.std_return,
            avg_drawdown=r.avg_drawdown,
            max_drawdown=r.max_drawdown,
            probability_of_ruin=r.probability_of_ruin,
            avg_consecutive_losses=r.avg_consecutive_losses,
            max_consecutive_losses=r.max_consecutive_losses,
            streak_confidence_intervals=r.streak_confidence_intervals,
            return_percentiles=r.return_percentiles,
            drawdown_percentiles=r.drawdown_percentiles,
            capital_requirements=r.capital_requirements,
        )

    @staticmethod
    def _validate_config(
        strategy_ids: list[str],
        simulation_count: int,
        starting_capital: float,
        sampling_method: str,
        ruin_thresholds: list[float],
        confidence_levels: list[float],
    ) -> None:
        if not strategy_ids:
            raise MonteCarloConfigException("At least one strategy_id is required.")
        if simulation_count < 100:
            raise MonteCarloConfigException("simulation_count must be at least 100.")
        if starting_capital <= 0:
            raise MonteCarloConfigException("starting_capital must be positive.")
        if sampling_method not in ("bootstrap", "random_shuffle", "replacement"):
            raise MonteCarloConfigException(
                f"Invalid sampling_method '{sampling_method}'."
            )
        for t in ruin_thresholds:
            if not (0.0 < t < 1.0):
                raise MonteCarloConfigException(
                    f"Each ruin_threshold must be in (0, 1); got {t}."
                )
        for c in confidence_levels:
            if not (0.0 < c < 1.0):
                raise MonteCarloConfigException(
                    f"Each confidence_level must be in (0, 1); got {c}."
                )
