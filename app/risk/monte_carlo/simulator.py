"""
Monte Carlo Simulation Engine.

Methodology
-----------
1. Take a historical sequence of executed trade P&Ls.
2. Run N independent simulations; each simulation:
   a. Randomly resample the trade sequence (shuffle / bootstrap / replacement).
   b. Replay trades chronologically starting from `starting_capital`.
   c. Track: equity curve, peak equity, max drawdown (abs + pct of starting capital),
      consecutive losing streaks, minimum equity reached.
3. Aggregate across all N simulations:
   - Return distribution (avg, median, best, worst, std, percentiles).
   - Drawdown distribution (avg, max, percentiles).
   - Probability of Ruin: P(min_equity < threshold × starting_capital).
   - Capital Requirements: min capital so that p95 drawdown doesn't breach threshold.
   - Losing Streak: avg, max, confidence intervals.

Drawdown Calculation
--------------------
Within each simulation:
  peak = max equity seen so far (starts at starting_capital)
  drawdown_abs  = peak - current_equity
  drawdown_pct  = drawdown_abs / starting_capital × 100  (vs starting capital, not peak)

Using starting_capital as denominator (not peak) gives the practitioner the answer
they care about: "how much of my *initial* capital did I lose at worst?"

Probability of Ruin
--------------------
For each threshold T ∈ ruin_thresholds (e.g. [0.50, 0.40, 0.30]):
  ruin_level = starting_capital × T
  ruin_event = min_equity_in_simulation ≤ ruin_level
  P(ruin @ T) = count(ruin_events) / simulation_count

A threshold of 0.50 means "account fell to 50% or below of starting capital."

Capital Requirement
--------------------
To survive the p95 worst-case drawdown while staying above threshold T:
  min_capital = p95_drawdown_abs / T

Example: p95 max drawdown = ₹3,00,000; threshold 0.50 → need ₹6,00,000 starting capital.
At that capital the worst 5% of scenarios would draw down ₹3,00,000 (50% of ₹6,00,000).

Portfolio Diversification Impact
----------------------------------
Handled at the service layer: trades from multiple strategies are merged before
passing to this engine. Comparing individual vs. merged P&Ls reveals the
diversification benefit (lower max drawdown, improved Sharpe).
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Optional

from app.risk.monte_carlo.trade_sampler import SamplingMethod, TradeSampler


# ── Configuration ──────────────────────────────────────────────────────────────

@dataclass
class MonteCarloConfig:
    """
    Configuration for a Monte Carlo simulation run.

    ruin_thresholds: fractions of starting_capital that trigger a "ruin" event.
        0.50 → account value ≤ 50% of start (lost ≥ 50%).
    confidence_levels: used for streak confidence-interval calculation.
    """
    starting_capital: float = 1_000_000.0
    simulation_count: int = 1000
    sampling_method: SamplingMethod = SamplingMethod.BOOTSTRAP
    ruin_thresholds: list[float] = field(
        default_factory=lambda: [0.50, 0.40, 0.30]
    )
    confidence_levels: list[float] = field(
        default_factory=lambda: [0.90, 0.95, 0.99]
    )
    seed: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "starting_capital": self.starting_capital,
            "simulation_count": self.simulation_count,
            "sampling_method": self.sampling_method.value,
            "ruin_thresholds": self.ruin_thresholds,
            "confidence_levels": self.confidence_levels,
            "seed": self.seed,
        }


# ── Per-simulation result ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class SimulationRun:
    """Immutable result of a single Monte Carlo simulation pass."""
    final_equity: float
    total_return: float
    max_drawdown_abs: float
    max_drawdown_pct: float      # % of starting_capital
    max_consecutive_losses: int
    min_equity: float


# ── Aggregate summary ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MonteCarloSummary:
    """Aggregated statistics from all N simulation runs."""
    simulation_count: int
    trade_count: int

    # Return distribution
    avg_return: float
    median_return: float
    best_return: float
    worst_return: float
    std_return: float

    # Drawdown distribution (% of starting_capital)
    avg_drawdown: float
    max_drawdown: float

    # Probability of ruin (keyed "50pct", "40pct", "30pct" etc.)
    probability_of_ruin: dict[str, float]

    # Losing streak statistics
    avg_consecutive_losses: float
    max_consecutive_losses: int

    # Confidence intervals for losing streaks
    streak_confidence_intervals: dict[str, dict[str, float]]

    # Distribution percentiles
    return_percentiles: dict[str, float]
    drawdown_percentiles: dict[str, float]

    # Minimum capital to survive p95 drawdown at each ruin threshold
    capital_requirements: dict[str, float]

    def to_dict(self) -> dict:
        return {
            "simulation_count": self.simulation_count,
            "trade_count": self.trade_count,
            "avg_return": self.avg_return,
            "median_return": self.median_return,
            "best_return": self.best_return,
            "worst_return": self.worst_return,
            "std_return": self.std_return,
            "avg_drawdown": self.avg_drawdown,
            "max_drawdown": self.max_drawdown,
            "probability_of_ruin": self.probability_of_ruin,
            "avg_consecutive_losses": self.avg_consecutive_losses,
            "max_consecutive_losses": self.max_consecutive_losses,
            "streak_confidence_intervals": self.streak_confidence_intervals,
            "return_percentiles": self.return_percentiles,
            "drawdown_percentiles": self.drawdown_percentiles,
            "capital_requirements": self.capital_requirements,
        }


# ── Simulator ─────────────────────────────────────────────────────────────────

class MonteCarloSimulator:
    """
    Runs N Monte Carlo simulations over a historical trade P&L sequence.

    Usage:
        config = MonteCarloConfig(starting_capital=1_000_000, simulation_count=1000)
        sim = MonteCarloSimulator(config)
        summary = sim.run(trade_pnls)
    """

    def __init__(self, config: MonteCarloConfig) -> None:
        self._config = config
        self._sampler = TradeSampler(seed=config.seed)

    def run(self, trade_pnls: list[float]) -> MonteCarloSummary:
        """
        Execute all simulations and return aggregated statistics.

        Args:
            trade_pnls: Executed trade P&Ls in chronological order.
                        NO_BREAKOUT (pnl=0) trades must be excluded by caller.

        Returns:
            MonteCarloSummary with all aggregate risk metrics.

        Raises:
            ValueError: when trade_pnls is empty.
        """
        if not trade_pnls:
            raise ValueError("trade_pnls must be non-empty for Monte Carlo simulation.")

        cfg = self._config
        runs: list[SimulationRun] = []

        for _ in range(cfg.simulation_count):
            sampled = self._sampler.sample(trade_pnls, cfg.sampling_method)
            run = self._simulate_one(sampled.pnls, cfg.starting_capital)
            runs.append(run)

        return self._aggregate(runs, cfg, len(trade_pnls))

    # ── Internal simulation pass ───────────────────────────────────────────────

    def _simulate_one(
        self,
        trade_pnls: list[float],
        starting_capital: float,
    ) -> SimulationRun:
        """Replay one randomized trade sequence tracking risk metrics."""
        equity = starting_capital
        peak = equity
        max_dd_abs = 0.0
        max_dd_pct = 0.0
        min_equity = equity
        consecutive_losses = 0
        max_consecutive_losses = 0

        for pnl in trade_pnls:
            equity += pnl

            if equity < min_equity:
                min_equity = equity

            if equity > peak:
                peak = equity
            else:
                dd_abs = peak - equity
                if dd_abs > max_dd_abs:
                    max_dd_abs = dd_abs
                    # Drawdown as % of starting capital (practitioner convention)
                    max_dd_pct = (
                        dd_abs / starting_capital * 100.0
                        if starting_capital > 0
                        else 0.0
                    )

            # Consecutive loss tracking (zero P&L counts as a loss)
            if pnl < 0:
                consecutive_losses += 1
                if consecutive_losses > max_consecutive_losses:
                    max_consecutive_losses = consecutive_losses
            else:
                consecutive_losses = 0

        return SimulationRun(
            final_equity=round(equity, 2),
            total_return=round(equity - starting_capital, 2),
            max_drawdown_abs=round(max_dd_abs, 2),
            max_drawdown_pct=round(max_dd_pct, 4),
            max_consecutive_losses=max_consecutive_losses,
            min_equity=round(min_equity, 2),
        )

    # ── Aggregation ───────────────────────────────────────────────────────────

    def _aggregate(
        self,
        runs: list[SimulationRun],
        cfg: MonteCarloConfig,
        trade_count: int,
    ) -> MonteCarloSummary:
        """Compute all aggregate statistics from N completed simulation runs."""
        n = len(runs)
        returns     = [r.total_return for r in runs]
        drawdowns   = [r.max_drawdown_pct for r in runs]
        drawdowns_a = [r.max_drawdown_abs for r in runs]
        streaks     = [r.max_consecutive_losses for r in runs]
        min_equs    = [r.min_equity for r in runs]

        # ── Returns ───────────────────────────────────────────────────────────
        avg_return    = round(statistics.mean(returns), 2)
        median_return = round(statistics.median(returns), 2)
        best_return   = round(max(returns), 2)
        worst_return  = round(min(returns), 2)
        std_return    = round(statistics.stdev(returns) if n >= 2 else 0.0, 2)

        # ── Drawdowns ─────────────────────────────────────────────────────────
        avg_drawdown = round(statistics.mean(drawdowns), 4)
        max_drawdown = round(max(drawdowns), 4)

        # p95 absolute drawdown is the anchor for capital requirement calculations
        p95_drawdown_abs = self._percentile(drawdowns_a, 0.95)

        # ── Probability of Ruin ───────────────────────────────────────────────
        # ruin_level = starting_capital × threshold
        # e.g. threshold=0.50 → ruin if equity ≤ 50% of start
        probability_of_ruin: dict[str, float] = {}
        capital_requirements: dict[str, float] = {}

        for threshold in cfg.ruin_thresholds:
            ruin_level = cfg.starting_capital * threshold
            ruin_count = sum(1 for me in min_equs if me <= ruin_level)
            key = f"{int(round(threshold * 100))}pct"
            probability_of_ruin[key] = round(ruin_count / n, 4)

            # Minimum capital to keep p95 drawdown within the ruin threshold.
            # We want: p95_drawdown / starting_capital ≤ threshold
            # → starting_capital ≥ p95_drawdown_abs / threshold
            capital_requirements[key] = (
                round(p95_drawdown_abs / threshold, 2) if threshold > 0 else float("inf")
            )

        # ── Losing Streaks ────────────────────────────────────────────────────
        avg_streak = round(statistics.mean(streaks), 2)
        max_streak = max(streaks)

        streak_cis: dict[str, dict[str, float]] = {}
        for level in cfg.confidence_levels:
            ci = self._confidence_interval(streaks, level)
            streak_cis[f"{int(round(level * 100))}pct"] = ci

        # ── Percentiles ───────────────────────────────────────────────────────
        return_percentiles  = self._compute_percentiles(returns)
        drawdown_percentiles = self._compute_percentiles(drawdowns)

        return MonteCarloSummary(
            simulation_count=n,
            trade_count=trade_count,
            avg_return=avg_return,
            median_return=median_return,
            best_return=best_return,
            worst_return=worst_return,
            std_return=std_return,
            avg_drawdown=avg_drawdown,
            max_drawdown=max_drawdown,
            probability_of_ruin=probability_of_ruin,
            avg_consecutive_losses=avg_streak,
            max_consecutive_losses=max_streak,
            streak_confidence_intervals=streak_cis,
            return_percentiles=return_percentiles,
            drawdown_percentiles=drawdown_percentiles,
            capital_requirements=capital_requirements,
        )

    # ── Static math helpers ───────────────────────────────────────────────────

    @staticmethod
    def _percentile(data: list[float], p: float) -> float:
        """Return the p-th percentile (p ∈ [0,1]) using linear interpolation."""
        if not data:
            return 0.0
        sd = sorted(data)
        n  = len(sd)
        idx  = p * (n - 1)
        low  = int(idx)
        high = min(low + 1, n - 1)
        frac = idx - low
        return round(sd[low] * (1.0 - frac) + sd[high] * frac, 4)

    @staticmethod
    def _confidence_interval(
        data: list[float],
        level: float,
    ) -> dict[str, float]:
        """
        Compute a two-sided confidence interval for the mean.

        Uses the normal approximation (reliable for n ≥ 30, which holds for
        any reasonable Monte Carlo run).
        """
        n = len(data)
        if n < 2:
            m = data[0] if data else 0.0
            return {"lower": round(m, 2), "mean": round(m, 2), "upper": round(m, 2)}

        m  = statistics.mean(data)
        s  = statistics.stdev(data)
        se = s / math.sqrt(n)

        z_map = {0.90: 1.645, 0.95: 1.960, 0.99: 2.576}
        z = z_map.get(level, 1.960)

        return {
            "lower": round(m - z * se, 2),
            "mean":  round(m, 2),
            "upper": round(m + z * se, 2),
        }

    @staticmethod
    def _compute_percentiles(data: list[float]) -> dict[str, float]:
        """Return a standard set of percentiles from a data list."""
        if not data:
            return {k: 0.0 for k in ("p10", "p25", "p50", "p75", "p90", "p95", "p99")}

        sd = sorted(data)
        n  = len(sd)

        def pct(p: float) -> float:
            idx  = p * (n - 1)
            low  = int(idx)
            high = min(low + 1, n - 1)
            frac = idx - low
            return round(sd[low] * (1.0 - frac) + sd[high] * frac, 2)

        return {
            "p10": pct(0.10),
            "p25": pct(0.25),
            "p50": pct(0.50),
            "p75": pct(0.75),
            "p90": pct(0.90),
            "p95": pct(0.95),
            "p99": pct(0.99),
        }
