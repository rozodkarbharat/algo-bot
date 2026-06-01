"""
Parameter optimization engine for the One-Side ORB strategy.

Pure Python — NO database calls, NO broker imports, NO I/O.
Receives pre-fetched data structures (identical to BacktestEngine inputs)
and sweeps strategy parameters to measure sensitivity of key metrics.

Optimization approach — UNIVARIATE SWEEP:
  Hold all parameters at base defaults; vary one parameter at a time.
  Produces per-parameter sensitivity curves rather than a combinatorial grid.
  This avoids the 4⁴ = 256 runs problem while still clearly showing WHERE
  each parameter drives or destroys edge.

  Example output for probability_threshold sweep:
    [{parameter_name: "probability_threshold", parameter_value: "0.60", win_rate: 0.54, ...},
     {parameter_name: "probability_threshold", parameter_value: "0.70", win_rate: 0.61, ...},
     {parameter_name: "probability_threshold", parameter_value: "0.80", win_rate: 0.68, ...}]

Performance:
  - Each sweep point runs one BacktestEngine — CPU-bound but fast (pure Python).
  - Call run_sweep() from a thread-pool executor to avoid blocking the event loop.
  - Pre-fetched data is SHARED across all sweep runs — no redundant I/O.

Scalability:
  - Add new sweep parameters by extending ParameterGrid and _build_sweep_configs().
  - Future full-grid mode: replace univariate loop with itertools.product().
  - Parallel sweep points: split config list across thread workers and merge.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from app.strategy.backtest_engine import BacktestConfig, BacktestEngine, BacktestEngineResult
from app.strategy.metrics_engine import MetricsEngine, MetricsResult
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ── Configuration ─────────────────────────────────────────────────────────────

@dataclass
class ParameterGrid:
    """
    Defines the sweep ranges for each strategy parameter.

    Each list represents the discrete values to test for that parameter.
    All other parameters are held at base defaults during each sweep.
    """

    # Continuation probability gate
    probability_thresholds: list[float] = field(
        default_factory=lambda: [0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90]
    )

    # ORB range filter (max first-candle range allowed)
    orb_range_filters: list[float] = field(
        default_factory=lambda: [0.50, 0.70, 1.00, 1.20, 1.50]
    )

    # Entry cut-off time (latest candle allowed for entry, IST HH:MM)
    entry_cutoff_times: list[str] = field(
        default_factory=lambda: ["10:00", "10:30", "11:00", "11:30"]
    )

    # Additional SL buffer beyond the ORB boundary
    sl_buffers: list[float] = field(
        default_factory=lambda: [0.00, 0.05, 0.10, 0.15]
    )


@dataclass
class ResearchConfig:
    """
    Complete configuration for a research / optimization run.

    Base parameters define the "default" run from which each sweep diverges.
    """

    # ── Date range ────────────────────────────────────────────────────────────
    from_date: date
    to_date: date
    symbols: Optional[list[str]] = None

    # ── Base strategy parameters (held fixed during each univariate sweep) ────
    base_probability_threshold: float = 0.70
    base_max_orb_range_pct: float = 1.00
    base_max_entry_time_ist: str = "11:30"
    base_sl_buffer_pct: float = 0.00
    capital_per_trade: float = 100_000.0
    slippage_pct: float = 0.05
    brokerage_per_side: float = 20.0

    # ── Sweep grids ───────────────────────────────────────────────────────────
    grid: ParameterGrid = field(default_factory=ParameterGrid)

    def to_dict(self) -> dict:
        return {
            "from_date": self.from_date.isoformat(),
            "to_date": self.to_date.isoformat(),
            "symbols": self.symbols,
            "base_probability_threshold": self.base_probability_threshold,
            "base_max_orb_range_pct": self.base_max_orb_range_pct,
            "base_max_entry_time_ist": self.base_max_entry_time_ist,
            "base_sl_buffer_pct": self.base_sl_buffer_pct,
            "capital_per_trade": self.capital_per_trade,
            "slippage_pct": self.slippage_pct,
            "brokerage_per_side": self.brokerage_per_side,
            "grid_probability_thresholds": self.grid.probability_thresholds,
            "grid_orb_range_filters": self.grid.orb_range_filters,
            "grid_entry_cutoff_times": self.grid.entry_cutoff_times,
            "grid_sl_buffers": self.grid.sl_buffers,
        }


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class OptimizationPoint:
    """
    Full metric snapshot for a single (parameter_name, parameter_value) run.

    Returned by ParameterOptimizer.run_sweep(); converted to
    ParameterOptimizationResult Beanie documents by ResearchService.
    """

    parameter_name: str
    parameter_value: str
    config: BacktestConfig           # full config used for this point
    metrics: MetricsResult           # all metrics from MetricsEngine


@dataclass
class SweepResult:
    """Aggregated output of a full parameter sweep."""

    run_id: str
    points: list[OptimizationPoint] = field(default_factory=list)
    total_configs_run: int = 0
    failed_configs: int = 0


# ── Engine ────────────────────────────────────────────────────────────────────

class ParameterOptimizer:
    """
    Sweeps strategy parameters over pre-fetched historical data.

    Usage (from thread-pool executor — this is CPU-bound):
        optimizer = ParameterOptimizer(research_config)
        result = optimizer.run_sweep(
            run_id="...",
            symbols=[...],
            prob_scores={...},
            osd_history={...},
            candle_history={...},
        )

    Data contracts match BacktestEngine exactly — ResearchService pre-fetches
    once and passes the same dicts to both BacktestEngine and ParameterOptimizer.
    """

    def __init__(self, config: ResearchConfig) -> None:
        self._config = config
        self._metrics_engine = MetricsEngine()

    # ── Public API ────────────────────────────────────────────────────────────

    def run_sweep(
        self,
        run_id: str,
        symbols: list[str],
        prob_scores: dict[str, float],
        osd_history: dict,
        candle_history: dict,
    ) -> SweepResult:
        """
        Execute the full univariate parameter sweep.

        For each parameter, hold all others at base defaults and iterate through
        that parameter's grid values. Each grid point is one BacktestEngine run.

        Args:
            run_id:        ResearchRun.run_id (for logging / result tagging).
            symbols:       Symbol list — same as BacktestService would use.
            prob_scores:   symbol → continuation_probability.
            osd_history:   symbol → date_str → OSD dict.
            candle_history: symbol → date_str → list[CandleData].

        Returns:
            SweepResult containing all OptimizationPoint records.
        """
        sweep = SweepResult(run_id=run_id)
        configs = self._build_sweep_configs()
        sweep.total_configs_run = len(configs)

        logger.info(
            "[%s] ParameterOptimizer: starting %d sweep points across %d symbols.",
            run_id,
            len(configs),
            len(symbols),
        )

        for param_name, param_value, backtest_config in configs:
            try:
                point = self._run_single_point(
                    run_id=run_id,
                    param_name=param_name,
                    param_value=param_value,
                    backtest_config=backtest_config,
                    symbols=symbols,
                    prob_scores=prob_scores,
                    osd_history=osd_history,
                    candle_history=candle_history,
                )
                sweep.points.append(point)
                logger.debug(
                    "[%s] ✓ %s=%s → trades=%d win_rate=%.2f%% pnl=₹%.0f",
                    run_id,
                    param_name,
                    param_value,
                    point.metrics.total_trades,
                    point.metrics.win_rate * 100,
                    point.metrics.total_pnl,
                )
            except Exception as exc:
                sweep.failed_configs += 1
                logger.error(
                    "[%s] sweep point %s=%s FAILED: %s",
                    run_id,
                    param_name,
                    param_value,
                    exc,
                )

        logger.info(
            "[%s] ParameterOptimizer: sweep complete — %d points, %d failed.",
            run_id,
            len(sweep.points),
            sweep.failed_configs,
        )
        return sweep

    # ── Internal ──────────────────────────────────────────────────────────────

    def _run_single_point(
        self,
        run_id: str,
        param_name: str,
        param_value: str,
        backtest_config: BacktestConfig,
        symbols: list[str],
        prob_scores: dict[str, float],
        osd_history: dict,
        candle_history: dict,
    ) -> OptimizationPoint:
        """Run one BacktestEngine configuration and return metrics."""
        engine = BacktestEngine(backtest_config)
        engine_result: BacktestEngineResult = engine.run(
            symbols=symbols,
            prob_scores=prob_scores,
            osd_history=osd_history,
            candle_history=candle_history,
        )
        metrics = self._metrics_engine.calculate(
            run_id=run_id,
            trades=engine_result.trades,
            total_candidate_days=engine_result.total_candidate_days,
        )
        return OptimizationPoint(
            parameter_name=param_name,
            parameter_value=param_value,
            config=backtest_config,
            metrics=metrics,
        )

    def _build_sweep_configs(self) -> list[tuple[str, str, BacktestConfig]]:
        """
        Build the list of (parameter_name, parameter_value_str, BacktestConfig) tuples.

        Returns one entry per sweep point. Each BacktestConfig varies exactly
        one parameter from the base while keeping all others at their defaults.
        """
        cfg = self._config
        entries: list[tuple[str, str, BacktestConfig]] = []

        # ── Probability threshold sweep ────────────────────────────────────
        for threshold in cfg.grid.probability_thresholds:
            entries.append((
                "probability_threshold",
                str(threshold),
                BacktestConfig(
                    from_date=cfg.from_date,
                    to_date=cfg.to_date,
                    symbols=cfg.symbols,
                    probability_threshold=threshold,
                    max_orb_range_pct=cfg.base_max_orb_range_pct,
                    max_entry_time_ist=cfg.base_max_entry_time_ist,
                    sl_buffer_pct=cfg.base_sl_buffer_pct,
                    capital_per_trade=cfg.capital_per_trade,
                    slippage_pct=cfg.slippage_pct,
                    brokerage_per_side=cfg.brokerage_per_side,
                ),
            ))

        # ── ORB range filter sweep ─────────────────────────────────────────
        for orb_range in cfg.grid.orb_range_filters:
            entries.append((
                "max_orb_range_pct",
                str(orb_range),
                BacktestConfig(
                    from_date=cfg.from_date,
                    to_date=cfg.to_date,
                    symbols=cfg.symbols,
                    probability_threshold=cfg.base_probability_threshold,
                    max_orb_range_pct=orb_range,
                    max_entry_time_ist=cfg.base_max_entry_time_ist,
                    sl_buffer_pct=cfg.base_sl_buffer_pct,
                    capital_per_trade=cfg.capital_per_trade,
                    slippage_pct=cfg.slippage_pct,
                    brokerage_per_side=cfg.brokerage_per_side,
                ),
            ))

        # ── Entry cutoff time sweep ────────────────────────────────────────
        for cutoff in cfg.grid.entry_cutoff_times:
            entries.append((
                "max_entry_time_ist",
                cutoff,
                BacktestConfig(
                    from_date=cfg.from_date,
                    to_date=cfg.to_date,
                    symbols=cfg.symbols,
                    probability_threshold=cfg.base_probability_threshold,
                    max_orb_range_pct=cfg.base_max_orb_range_pct,
                    max_entry_time_ist=cutoff,
                    sl_buffer_pct=cfg.base_sl_buffer_pct,
                    capital_per_trade=cfg.capital_per_trade,
                    slippage_pct=cfg.slippage_pct,
                    brokerage_per_side=cfg.brokerage_per_side,
                ),
            ))

        # ── SL buffer sweep ───────────────────────────────────────────────
        for sl_buf in cfg.grid.sl_buffers:
            entries.append((
                "sl_buffer_pct",
                str(sl_buf),
                BacktestConfig(
                    from_date=cfg.from_date,
                    to_date=cfg.to_date,
                    symbols=cfg.symbols,
                    probability_threshold=cfg.base_probability_threshold,
                    max_orb_range_pct=cfg.base_max_orb_range_pct,
                    max_entry_time_ist=cfg.base_max_entry_time_ist,
                    sl_buffer_pct=sl_buf,
                    capital_per_trade=cfg.capital_per_trade,
                    slippage_pct=cfg.slippage_pct,
                    brokerage_per_side=cfg.brokerage_per_side,
                ),
            ))

        return entries
