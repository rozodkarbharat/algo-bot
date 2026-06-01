"""
Walk-Forward Engine for the One-Side ORB strategy.

Pure Python — NO database calls, NO broker imports, NO I/O.
Receives pre-fetched data (same format as BacktestEngine / ParameterOptimizer)
and executes a full walk-forward loop over a set of pre-generated windows.

Algorithm per window
--------------------
1. Filter osd_history and candle_history to the training date range.
2. Build a ResearchConfig for the training window using WalkForwardConfig base params.
3. Run ParameterOptimizer.run_sweep() on training data.
4. Select the sweep point with the highest Sharpe ratio → best params.
5. Filter data to the testing date range (+ 10-day OSD lookback for "yesterday" logic).
6. Override BacktestConfig dates to the testing window.
7. Run BacktestEngine on testing data with best params → trades.
8. Run MetricsEngine on the resulting trades → OOS MetricsResult.
9. Return a SegmentResult for the window.

Segment failures (e.g. no training data, no sweep points) are caught per-window:
the error is recorded in SegmentResult.error and the loop continues.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from app.research.parameter_optimizer import ParameterOptimizer, ResearchConfig, ParameterGrid
from app.research.walk_forward.window_generator import WalkForwardConfig, WalkForwardWindow
from app.strategy.backtest_engine import BacktestConfig, BacktestEngine
from app.strategy.metrics_engine import MetricsEngine, MetricsResult
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class SegmentResult:
    """Holds the full output for one walk-forward window."""
    window: WalkForwardWindow
    selected_parameters: dict          # best params from training optimisation
    optimization_sharpe: float         # Sharpe of selected params in training
    oos_metrics: MetricsResult         # out-of-sample test metrics
    oos_trades: list                   # list[SimulatedTrade] from testing window
    error: Optional[str] = None        # set if this segment failed

    def to_dict(self) -> dict:
        return {
            "segment_number": self.window.segment_number,
            "training_start": self.window.training_start.isoformat(),
            "training_end": self.window.training_end.isoformat(),
            "testing_start": self.window.testing_start.isoformat(),
            "testing_end": self.window.testing_end.isoformat(),
            "selected_parameters": self.selected_parameters,
            "optimization_sharpe": self.optimization_sharpe,
            "oos_total_trades": self.oos_metrics.total_trades,
            "oos_total_pnl": self.oos_metrics.total_pnl,
            "oos_win_rate": self.oos_metrics.win_rate,
            "oos_sharpe_ratio": self.oos_metrics.sharpe_ratio,
            "oos_max_drawdown": self.oos_metrics.max_drawdown,
            "oos_profit_factor": self.oos_metrics.profit_factor,
            "error": self.error,
        }


@dataclass
class WalkForwardEngineResult:
    """Aggregated output of a complete walk-forward run."""
    run_id: str
    windows: list[WalkForwardWindow] = field(default_factory=list)
    segments: list[SegmentResult] = field(default_factory=list)
    failed_segments: int = 0

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "total_windows": len(self.windows),
            "total_segments": len(self.segments),
            "failed_segments": self.failed_segments,
            "segments": [s.to_dict() for s in self.segments],
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _filter_history(history: dict, start: date, end: date) -> dict:
    """
    Filter a nested history dict to only include date keys within [start, end].

    Args:
        history: symbol -> date_str -> value
        start:   inclusive lower bound
        end:     inclusive upper bound

    Returns:
        A new dict with the same structure but only date_strs in [start, end].
    """
    result: dict = {}
    for symbol, date_dict in history.items():
        filtered = {
            d: v
            for d, v in date_dict.items()
            if start <= date.fromisoformat(d) <= end
        }
        result[symbol] = filtered
    return result


# ── Engine ────────────────────────────────────────────────────────────────────

class WalkForwardEngine:
    """
    Executes a full walk-forward validation loop.

    Usage (from thread-pool executor — CPU-bound):
        engine = WalkForwardEngine(config)
        result = engine.run(
            run_id="wf_abc123",
            windows=generator.generate(),
            symbols=["RELIANCE", "TCS", ...],
            prob_scores={"RELIANCE": 0.72, ...},
            osd_history=osd_history_dict,
            candle_history=candle_history_dict,
        )
    """

    def __init__(self, config: WalkForwardConfig) -> None:
        self._config = config

    # ── Public API ────────────────────────────────────────────────────────────

    def run(
        self,
        run_id: str,
        windows: list[WalkForwardWindow],
        symbols: list[str],
        prob_scores: dict[str, float],
        osd_history: dict,
        candle_history: dict,
    ) -> WalkForwardEngineResult:
        """
        Execute the walk-forward loop over all pre-generated windows.

        Failures in individual segments are caught and logged; the loop
        always continues to the next window so one bad window does not abort
        the entire analysis.

        Args:
            run_id:        Identifier for this walk-forward run (for logging).
            windows:       Pre-generated list of WalkForwardWindow (from WalkForwardWindowGenerator).
            symbols:       Symbol universe to evaluate.
            prob_scores:   symbol -> continuation_probability.
            osd_history:   symbol -> date_str -> OSD dict (full date range).
            candle_history: symbol -> date_str -> list[CandleData] (full date range).

        Returns:
            WalkForwardEngineResult with a SegmentResult per window.
        """
        wf_result = WalkForwardEngineResult(run_id=run_id, windows=list(windows))

        logger.info(
            "[%s] WalkForwardEngine: starting %d windows over %d symbols.",
            run_id,
            len(windows),
            len(symbols),
        )

        for window in windows:
            try:
                segment = self._process_segment(
                    run_id=run_id,
                    window=window,
                    symbols=symbols,
                    prob_scores=prob_scores,
                    osd_history=osd_history,
                    candle_history=candle_history,
                )
                wf_result.segments.append(segment)
                logger.info(
                    "[%s] Window #%d done — OOS trades=%d pnl=%.0f sharpe=%s",
                    run_id,
                    window.segment_number,
                    segment.oos_metrics.total_trades,
                    segment.oos_metrics.total_pnl,
                    segment.oos_metrics.sharpe_ratio,
                )
            except Exception as exc:
                wf_result.failed_segments += 1
                error_msg = f"Window #{window.segment_number} failed: {exc}"
                logger.error("[%s] %s", run_id, error_msg, exc_info=True)
                # Build a placeholder SegmentResult so the window is still represented
                wf_result.segments.append(
                    SegmentResult(
                        window=window,
                        selected_parameters={},
                        optimization_sharpe=0.0,
                        oos_metrics=MetricsResult(run_id=run_id),
                        oos_trades=[],
                        error=error_msg,
                    )
                )

        logger.info(
            "[%s] WalkForwardEngine: complete — %d/%d segments succeeded, %d failed.",
            run_id,
            len(windows) - wf_result.failed_segments,
            len(windows),
            wf_result.failed_segments,
        )
        return wf_result

    # ── Internal ──────────────────────────────────────────────────────────────

    def _process_segment(
        self,
        run_id: str,
        window: WalkForwardWindow,
        symbols: list[str],
        prob_scores: dict[str, float],
        osd_history: dict,
        candle_history: dict,
    ) -> SegmentResult:
        """
        Process a single walk-forward window end-to-end.

        Steps
        -----
        1. Slice data to training date range.
        2. Build ResearchConfig; run ParameterOptimizer sweep on training data.
        3. Select the best sweep point (highest Sharpe; fallback to 0.0).
        4. Slice data to testing date range (+10-day OSD lookback).
        5. Override dates in best BacktestConfig to the testing window.
        6. Run BacktestEngine on testing data.
        7. Compute OOS metrics via MetricsEngine.
        8. Return SegmentResult.

        Raises:
            ValueError: if the optimizer returns no valid sweep points.
        """
        cfg = self._config
        seg_num = window.segment_number

        # ── Step 1: Training data ─────────────────────────────────────────────
        logger.debug(
            "[%s] Window #%d: filtering training data [%s, %s].",
            run_id, seg_num,
            window.training_start.isoformat(),
            window.training_end.isoformat(),
        )
        training_osd = _filter_history(osd_history, window.training_start, window.training_end)
        training_candles = _filter_history(candle_history, window.training_start, window.training_end)

        # ── Step 2: Build ResearchConfig for training window ──────────────────
        research_config = ResearchConfig(
            from_date=window.training_start,
            to_date=window.training_end,
            symbols=cfg.symbols,
            base_probability_threshold=cfg.base_probability_threshold,
            base_max_orb_range_pct=cfg.base_max_orb_range_pct,
            base_max_entry_time_ist=cfg.base_max_entry_time_ist,
            base_sl_buffer_pct=cfg.base_sl_buffer_pct,
            capital_per_trade=cfg.capital_per_trade,
            slippage_pct=cfg.slippage_pct,
            brokerage_per_side=cfg.brokerage_per_side,
            grid=ParameterGrid(),
        )

        # ── Step 3: Run ParameterOptimizer on training data ───────────────────
        logger.debug(
            "[%s] Window #%d: running ParameterOptimizer sweep on training data.",
            run_id, seg_num,
        )
        optimizer = ParameterOptimizer(research_config)
        sweep = optimizer.run_sweep(
            run_id=run_id,
            symbols=symbols,
            prob_scores=prob_scores,
            osd_history=training_osd,
            candle_history=training_candles,
        )

        if not sweep.points:
            raise ValueError(
                f"Window #{seg_num}: ParameterOptimizer returned no sweep points — "
                "training window may have insufficient data."
            )

        # Select best point by Sharpe (treat None Sharpe as 0.0)
        best_point = max(
            sweep.points,
            key=lambda p: (p.metrics.sharpe_ratio or 0.0),
        )
        opt_sharpe = best_point.metrics.sharpe_ratio or 0.0

        logger.debug(
            "[%s] Window #%d: best params — %s=%s sharpe=%.4f",
            run_id, seg_num,
            best_point.parameter_name,
            best_point.parameter_value,
            opt_sharpe,
        )

        # ── Step 4: Extract best BacktestConfig ───────────────────────────────
        best_config: BacktestConfig = best_point.config
        selected_params = {
            "parameter_name": best_point.parameter_name,
            "parameter_value": best_point.parameter_value,
            "probability_threshold": best_config.probability_threshold,
            "max_orb_range_pct": best_config.max_orb_range_pct,
            "max_entry_time_ist": best_config.max_entry_time_ist,
            "sl_buffer_pct": best_config.sl_buffer_pct,
        }

        # ── Step 5: Testing data (with 10-day OSD lookback) ───────────────────
        lookback_start = window.testing_start - timedelta(days=10)
        testing_osd = _filter_history(osd_history, lookback_start, window.testing_end)
        testing_candles = _filter_history(candle_history, window.testing_start, window.testing_end)

        logger.debug(
            "[%s] Window #%d: filtering testing data [%s, %s] (OSD lookback from %s).",
            run_id, seg_num,
            window.testing_start.isoformat(),
            window.testing_end.isoformat(),
            lookback_start.isoformat(),
        )

        # ── Step 6: Override date range to testing window ─────────────────────
        test_config = BacktestConfig(
            from_date=window.testing_start,
            to_date=window.testing_end,
            symbols=best_config.symbols,
            probability_threshold=best_config.probability_threshold,
            min_move_percent=best_config.min_move_percent,
            max_orb_range_pct=best_config.max_orb_range_pct,
            max_entry_time_ist=best_config.max_entry_time_ist,
            capital_per_trade=best_config.capital_per_trade,
            slippage_pct=best_config.slippage_pct,
            brokerage_per_side=best_config.brokerage_per_side,
            sl_buffer_pct=best_config.sl_buffer_pct,
        )

        # ── Step 7: Run BacktestEngine on testing window ──────────────────────
        logger.debug(
            "[%s] Window #%d: running BacktestEngine on testing window.",
            run_id, seg_num,
        )
        engine = BacktestEngine(test_config)
        engine_result = engine.run(
            symbols=symbols,
            prob_scores=prob_scores,
            osd_history=testing_osd,
            candle_history=testing_candles,
        )

        # ── Step 8: Compute OOS metrics ───────────────────────────────────────
        oos_metrics = MetricsEngine().calculate(
            run_id=run_id,
            trades=engine_result.trades,
            total_candidate_days=engine_result.total_candidate_days,
        )

        # ── Step 9: Return SegmentResult ─────────────────────────────────────
        return SegmentResult(
            window=window,
            selected_parameters=selected_params,
            optimization_sharpe=opt_sharpe,
            oos_metrics=oos_metrics,
            oos_trades=engine_result.trades,
        )
