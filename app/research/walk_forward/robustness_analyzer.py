"""
Robustness Analyzer for the One-Side ORB walk-forward results.

Pure Python — NO database calls, NO broker imports, NO I/O.
Standard library only: math, statistics.

Computes three dimensions of robustness and combines them into a single
0-100 score:

  1. Parameter Stability (30%)
     How consistently does the optimizer select similar parameter values
     across windows?  High variance → lower score.

  2. Performance Consistency (40%)
     How stable is the out-of-sample P&L across windows?
     Measured via the coefficient of variation (CV = std / |mean|).

  3. Regime Sensitivity (30%)
     How wide is the spread between the best and worst windows?
     A strategy that performs wildly differently across regimes is fragile.

At least 2 completed segments are required to compute variance-based scores.
With fewer than 2 completed segments all dimension scores default to 50.0
(neutral / insufficient data).
"""

import math
import statistics
from dataclasses import dataclass, field
from typing import Optional

from app.research.walk_forward.engine import SegmentResult
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Robustness score weights (must sum to 1.0)
_WEIGHT_STABILITY    = 0.30
_WEIGHT_CONSISTENCY  = 0.40
_WEIGHT_REGIME       = 0.30

_NEUTRAL_SCORE = 50.0   # default when insufficient data


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class RobustnessResult:
    """
    Comprehensive robustness assessment for a walk-forward run.

    All scores are in [0.0, 100.0] where higher = more robust.
    """

    # Overall composite score
    robustness_score: float = 0.0               # weighted average of the three dimensions

    # Dimension scores
    parameter_stability_score: float = 0.0      # 0-100: how consistent selected params are
    performance_consistency_score: float = 0.0  # 0-100: low CV in returns
    regime_sensitivity_score: float = 0.0       # 0-100 (100 = not sensitive to regimes)

    # ── Parameter stability details ───────────────────────────────────────────
    parameter_variance: dict[str, float] = field(default_factory=dict)
    # param_name -> std_dev across windows

    most_stable_parameters: list[str] = field(default_factory=list)
    # parameter keys sorted ascending by coefficient-of-variation

    least_stable_parameters: list[str] = field(default_factory=list)
    # parameter keys sorted descending by coefficient-of-variation

    # ── Performance consistency details ───────────────────────────────────────
    return_coefficient_of_variation: float = 0.0   # std(pnls) / |mean(pnls)|
    profitable_segments_pct: float = 0.0            # % of completed segments with PnL > 0

    # ── Regime sensitivity details ─────────────────────────────────────────────
    best_window_pnl: float = 0.0
    worst_window_pnl: float = 0.0
    pnl_range_pct: float = 0.0                      # (best - worst) / |mean| * 100

    def to_dict(self) -> dict:
        return {
            "robustness_score": self.robustness_score,
            "parameter_stability_score": self.parameter_stability_score,
            "performance_consistency_score": self.performance_consistency_score,
            "regime_sensitivity_score": self.regime_sensitivity_score,
            "parameter_variance": self.parameter_variance,
            "most_stable_parameters": self.most_stable_parameters,
            "least_stable_parameters": self.least_stable_parameters,
            "return_coefficient_of_variation": self.return_coefficient_of_variation,
            "profitable_segments_pct": self.profitable_segments_pct,
            "best_window_pnl": self.best_window_pnl,
            "worst_window_pnl": self.worst_window_pnl,
            "pnl_range_pct": self.pnl_range_pct,
        }


# ── Analyzer ──────────────────────────────────────────────────────────────────

class RobustnessAnalyzer:
    """
    Computes how robust the walk-forward strategy is across windows.

    Usage:
        analyzer = RobustnessAnalyzer()
        result   = analyzer.analyze(wf_engine_result.segments)
    """

    def analyze(self, segments: list[SegmentResult]) -> RobustnessResult:
        """
        Compute the full robustness assessment.

        Args:
            segments: All SegmentResult records from WalkForwardEngine.run().
                      Failed segments (error is not None) are excluded.

        Returns:
            RobustnessResult with scores and supporting detail.
        """
        result = RobustnessResult()

        completed = [s for s in segments if s.error is None]

        if len(completed) < 2:
            logger.warning(
                "RobustnessAnalyzer: only %d completed segment(s) — "
                "setting all dimension scores to %.1f (insufficient data).",
                len(completed),
                _NEUTRAL_SCORE,
            )
            result.parameter_stability_score    = _NEUTRAL_SCORE
            result.performance_consistency_score = _NEUTRAL_SCORE
            result.regime_sensitivity_score      = _NEUTRAL_SCORE
            result.robustness_score = _NEUTRAL_SCORE
            if completed:
                pnl = completed[0].oos_metrics.total_pnl
                result.best_window_pnl  = pnl
                result.worst_window_pnl = pnl
                result.profitable_segments_pct = 100.0 if pnl > 0 else 0.0
            return result

        # ── 1. Parameter stability ─────────────────────────────────────────────
        result.parameter_stability_score, param_detail = self._score_parameter_stability(
            completed
        )
        result.parameter_variance          = param_detail["variance"]
        result.most_stable_parameters      = param_detail["most_stable"]
        result.least_stable_parameters     = param_detail["least_stable"]

        # ── 2. Performance consistency ─────────────────────────────────────────
        pnls = [s.oos_metrics.total_pnl for s in completed]
        result.performance_consistency_score, consistency_detail = (
            self._score_performance_consistency(pnls)
        )
        result.return_coefficient_of_variation = consistency_detail["cv"]
        result.profitable_segments_pct         = consistency_detail["profitable_pct"]

        # ── 3. Regime sensitivity ──────────────────────────────────────────────
        result.regime_sensitivity_score, regime_detail = (
            self._score_regime_sensitivity(pnls)
        )
        result.best_window_pnl  = regime_detail["best_pnl"]
        result.worst_window_pnl = regime_detail["worst_pnl"]
        result.pnl_range_pct    = regime_detail["range_pct"]

        # ── Composite score ────────────────────────────────────────────────────
        result.robustness_score = round(
            _WEIGHT_STABILITY   * result.parameter_stability_score
            + _WEIGHT_CONSISTENCY * result.performance_consistency_score
            + _WEIGHT_REGIME      * result.regime_sensitivity_score,
            2,
        )

        logger.info(
            "RobustnessAnalyzer: robustness=%.1f "
            "(stability=%.1f, consistency=%.1f, regime=%.1f)",
            result.robustness_score,
            result.parameter_stability_score,
            result.performance_consistency_score,
            result.regime_sensitivity_score,
        )
        return result

    # ── Dimension scorers ─────────────────────────────────────────────────────

    @staticmethod
    def _score_parameter_stability(
        completed: list[SegmentResult],
    ) -> tuple[float, dict]:
        """
        Score parameter stability across windows.

        Algorithm:
          For each numeric/string parameter key in selected_parameters, collect
          the values across windows.  For numeric parameters compute the
          coefficient of variation (CV = std / |mean|).  For string/categorical
          parameters compute the fraction of windows where the value differs
          from the mode.

          Mean CV across all parameters is the instability signal.
          Score = max(0, 100 - mean_cv * 100), clamped to [0, 100].

        Returns:
            (score, {"variance": {param: std_dev}, "most_stable": [...], "least_stable": [...]})
        """
        # Collect param keys from the first completed segment
        all_param_keys: list[str] = list(completed[0].selected_parameters.keys())

        # Exclude metadata keys that are not strategy hyperparameters
        _META_KEYS = {"parameter_name", "parameter_value"}
        param_keys = [k for k in all_param_keys if k not in _META_KEYS]

        if not param_keys:
            return _NEUTRAL_SCORE, {"variance": {}, "most_stable": [], "least_stable": []}

        param_cv: dict[str, float] = {}
        param_std: dict[str, float] = {}

        for key in param_keys:
            raw_values = [
                seg.selected_parameters.get(key) for seg in completed
                if seg.selected_parameters.get(key) is not None
            ]
            if not raw_values:
                continue

            # Try to treat as numeric
            try:
                numeric = [float(v) for v in raw_values]
                if len(numeric) >= 2:
                    std = statistics.stdev(numeric)
                    mean = statistics.mean(numeric)
                    cv = std / abs(mean) if abs(mean) > 1e-9 else (1.0 if std > 0 else 0.0)
                else:
                    std = 0.0
                    cv  = 0.0
                param_std[key] = round(std, 6)
                param_cv[key]  = cv
            except (TypeError, ValueError):
                # Categorical — instability = fraction of windows that differ from mode
                from collections import Counter
                mode_val = Counter(raw_values).most_common(1)[0][0]
                diff_frac = sum(1 for v in raw_values if v != mode_val) / len(raw_values)
                param_std[key] = round(diff_frac, 6)
                param_cv[key]  = diff_frac

        if not param_cv:
            return _NEUTRAL_SCORE, {"variance": param_std, "most_stable": [], "least_stable": []}

        mean_cv = statistics.mean(param_cv.values())
        score   = max(0.0, min(100.0, 100.0 - mean_cv * 100.0))

        # Sort params by CV for reporting
        sorted_by_cv = sorted(param_cv.keys(), key=lambda k: param_cv[k])
        most_stable  = sorted_by_cv[:3]
        least_stable = list(reversed(sorted_by_cv))[:3]

        return round(score, 2), {
            "variance":     param_std,
            "most_stable":  most_stable,
            "least_stable": least_stable,
        }

    @staticmethod
    def _score_performance_consistency(
        pnls: list[float],
    ) -> tuple[float, dict]:
        """
        Score performance consistency from segment PnLs.

        CV = std(pnls) / max(|mean(pnls)|, 1)
        Score = max(0, 100 - cv * 100), clamped to [0, 100].

        A strategy with consistent positive returns scores near 100.
        High variance (alternating big wins / big losses) scores near 0.

        Returns:
            (score, {"cv": float, "profitable_pct": float})
        """
        if len(pnls) < 2:
            profitable_pct = 100.0 if (pnls and pnls[0] > 0) else 0.0
            return _NEUTRAL_SCORE, {"cv": 0.0, "profitable_pct": profitable_pct}

        std  = statistics.stdev(pnls)
        mean = statistics.mean(pnls)
        cv   = std / max(abs(mean), 1.0)

        score = max(0.0, min(100.0, 100.0 - cv * 100.0))
        profitable_pct = round(sum(1 for p in pnls if p > 0) / len(pnls) * 100.0, 2)

        return round(score, 2), {"cv": round(cv, 4), "profitable_pct": profitable_pct}

    @staticmethod
    def _score_regime_sensitivity(
        pnls: list[float],
    ) -> tuple[float, dict]:
        """
        Score regime sensitivity from segment PnLs.

        range_pct = (best_pnl - worst_pnl) / max(|mean_pnl|, 1) * 100
        Score = max(0, 100 - range_pct), clamped to [0, 100].

        A small spread (all windows perform similarly) → score near 100.
        A large spread (strategy collapses in some regimes) → score near 0.

        Returns:
            (score, {"best_pnl": float, "worst_pnl": float, "range_pct": float})
        """
        best_pnl  = max(pnls)
        worst_pnl = min(pnls)
        mean_pnl  = statistics.mean(pnls)

        range_pct = (best_pnl - worst_pnl) / max(abs(mean_pnl), 1.0) * 100.0
        score     = max(0.0, min(100.0, 100.0 - range_pct))

        return round(score, 2), {
            "best_pnl":  round(best_pnl, 2),
            "worst_pnl": round(worst_pnl, 2),
            "range_pct": round(range_pct, 4),
        }
