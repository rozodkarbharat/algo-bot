"""
Monte Carlo Report Generator.

Produces four structured report dicts from stored MonteCarloSummary data.
All methods are pure Python — no I/O, no DB.

Reports:
  risk_report              — Overall risk summary with probability-of-ruin table.
  drawdown_report          — Detailed drawdown percentiles and worst-case analysis.
  capital_requirement_report — Minimum capital needed at each ruin threshold.
  strategy_comparison_report — Side-by-side strategy vs. combined portfolio view.
"""

from __future__ import annotations

from typing import Any

from app.risk.monte_carlo.simulator import MonteCarloSummary


class ReportGenerator:
    """Generates human-readable report dicts from MonteCarloSummary objects."""

    # ── Public report generators ──────────────────────────────────────────────

    def generate_risk_report(
        self,
        summary: MonteCarloSummary,
        strategy_label: str,
        starting_capital: float,
    ) -> dict[str, Any]:
        """
        Overall risk summary report.

        Includes: return distribution, probability-of-ruin table, losing-streak
        statistics, and headline risk rating.
        """
        ruin = summary.probability_of_ruin
        risk_rating = self._risk_rating(ruin)

        return {
            "report_type": "risk_report",
            "strategy": strategy_label,
            "starting_capital": starting_capital,
            "simulation_count": summary.simulation_count,
            "trade_count": summary.trade_count,
            "return_summary": {
                "avg_return": summary.avg_return,
                "median_return": summary.median_return,
                "best_return": summary.best_return,
                "worst_return": summary.worst_return,
                "std_return": summary.std_return,
                "avg_return_pct": round(
                    summary.avg_return / starting_capital * 100, 2
                ) if starting_capital else 0.0,
            },
            "drawdown_summary": {
                "avg_drawdown_pct": summary.avg_drawdown,
                "max_drawdown_pct": summary.max_drawdown,
                "avg_drawdown_abs": round(
                    summary.avg_drawdown / 100 * starting_capital, 2
                ),
            },
            "probability_of_ruin": {
                k: {
                    "probability": v,
                    "probability_pct": round(v * 100, 2),
                    "interpretation": self._ruin_interpretation(v),
                }
                for k, v in ruin.items()
            },
            "losing_streak": {
                "avg_consecutive_losses": summary.avg_consecutive_losses,
                "max_consecutive_losses": summary.max_consecutive_losses,
                "confidence_intervals": summary.streak_confidence_intervals,
            },
            "risk_rating": risk_rating,
        }

    def generate_drawdown_report(
        self,
        summary: MonteCarloSummary,
        strategy_label: str,
        starting_capital: float,
    ) -> dict[str, Any]:
        """
        Detailed drawdown analysis report.

        Includes: percentile distribution, worst-case analysis, and recovery
        expectations based on the return distribution.
        """
        dd_pct  = summary.drawdown_percentiles
        ret_pct = summary.return_percentiles

        # Estimate recovery time: if avg return per N trades is known,
        # how long to recover from the expected drawdown?
        avg_return_per_trade = (
            summary.avg_return / summary.trade_count if summary.trade_count > 0 else 0
        )
        avg_dd_abs = round(summary.avg_drawdown / 100 * starting_capital, 2)
        expected_recovery_trades = (
            round(avg_dd_abs / avg_return_per_trade)
            if avg_return_per_trade > 0 and avg_dd_abs > 0
            else None
        )

        return {
            "report_type": "drawdown_report",
            "strategy": strategy_label,
            "starting_capital": starting_capital,
            "simulation_count": summary.simulation_count,
            "drawdown_percentiles_pct": dd_pct,
            "drawdown_percentiles_abs": {
                k: round(v / 100 * starting_capital, 2)
                for k, v in dd_pct.items()
            },
            "worst_case_analysis": {
                "max_drawdown_pct": summary.max_drawdown,
                "max_drawdown_abs": round(summary.max_drawdown / 100 * starting_capital, 2),
                "p99_drawdown_pct": dd_pct.get("p99", 0.0),
                "p95_drawdown_pct": dd_pct.get("p95", 0.0),
                "p95_drawdown_abs": round(
                    dd_pct.get("p95", 0.0) / 100 * starting_capital, 2
                ),
            },
            "expected_drawdown": {
                "avg_drawdown_pct": summary.avg_drawdown,
                "median_drawdown_pct": dd_pct.get("p50", 0.0),
                "avg_drawdown_abs": avg_dd_abs,
            },
            "recovery_analysis": {
                "avg_return_per_trade": round(avg_return_per_trade, 2),
                "expected_trades_to_recover": expected_recovery_trades,
                "note": (
                    "Estimated trades to recover from average drawdown at the "
                    "current average return per trade."
                ),
            },
            "return_vs_drawdown": {
                "return_p50": ret_pct.get("p50", 0.0),
                "return_p25": ret_pct.get("p25", 0.0),
                "worst_return_to_worst_drawdown_ratio": round(
                    abs(summary.worst_return) / max(
                        summary.max_drawdown / 100 * starting_capital, 1
                    ),
                    4,
                ),
            },
        }

    def generate_capital_requirement_report(
        self,
        summary: MonteCarloSummary,
        strategy_label: str,
        starting_capital: float,
    ) -> dict[str, Any]:
        """
        Minimum capital requirement report.

        Estimates the starting capital needed so that the p95 worst-case drawdown
        stays within each ruin threshold.

        Formula: min_capital = p95_drawdown_abs / threshold
        """
        p95_dd_pct = summary.drawdown_percentiles.get("p95", summary.max_drawdown)
        p95_dd_abs = round(p95_dd_pct / 100 * starting_capital, 2)

        cap_reqs = summary.capital_requirements
        current_at_risk = {
            k: {
                "min_capital_required": v,
                "current_capital_sufficient": starting_capital >= v,
                "surplus_or_deficit": round(starting_capital - v, 2),
                "margin_of_safety_pct": round(
                    (starting_capital - v) / v * 100, 2
                ) if v > 0 else 0.0,
            }
            for k, v in cap_reqs.items()
        }

        return {
            "report_type": "capital_requirement_report",
            "strategy": strategy_label,
            "current_starting_capital": starting_capital,
            "p95_worst_case_drawdown_abs": p95_dd_abs,
            "p95_worst_case_drawdown_pct": p95_dd_pct,
            "capital_requirements": current_at_risk,
            "recommendation": self._capital_recommendation(
                starting_capital, cap_reqs
            ),
        }

    def generate_strategy_comparison_report(
        self,
        strategy_summaries: dict[str, MonteCarloSummary],
        portfolio_summary: MonteCarloSummary,
        starting_capital: float,
    ) -> dict[str, Any]:
        """
        Side-by-side comparison of individual strategies vs. combined portfolio.

        Highlights the diversification benefit: reduction in drawdown and
        probability of ruin when running strategies simultaneously.
        """
        rows: list[dict] = []

        for label, s in strategy_summaries.items():
            rows.append(self._strategy_row(label, s, starting_capital))

        portfolio_row = self._strategy_row("Combined Portfolio", portfolio_summary, starting_capital)

        # Diversification benefit vs. the single worst-performing strategy
        if rows:
            worst_dd  = max(r["max_drawdown_pct"] for r in rows)
            combo_dd  = portfolio_summary.max_drawdown
            dd_reduction = round(worst_dd - combo_dd, 4)

            worst_ruin_50 = max(
                r["probability_of_ruin"].get("50pct", 0) for r in rows
            )
            combo_ruin_50 = portfolio_summary.probability_of_ruin.get("50pct", 0)
            ruin_reduction = round(worst_ruin_50 - combo_ruin_50, 4)
        else:
            dd_reduction   = 0.0
            ruin_reduction = 0.0

        return {
            "report_type": "strategy_comparison_report",
            "starting_capital": starting_capital,
            "strategies": rows,
            "portfolio": portfolio_row,
            "diversification_benefit": {
                "drawdown_reduction_pct": dd_reduction,
                "ruin_50pct_reduction": ruin_reduction,
                "note": (
                    "Reduction in max drawdown and probability of ruin by "
                    "running strategies together vs. worst individual strategy."
                ),
            },
        }

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _strategy_row(
        label: str, s: MonteCarloSummary, starting_capital: float
    ) -> dict:
        return {
            "strategy": label,
            "trade_count": s.trade_count,
            "avg_return": s.avg_return,
            "avg_return_pct": round(
                s.avg_return / starting_capital * 100, 2
            ) if starting_capital else 0.0,
            "max_drawdown_pct": s.max_drawdown,
            "avg_drawdown_pct": s.avg_drawdown,
            "probability_of_ruin": s.probability_of_ruin,
            "avg_consecutive_losses": s.avg_consecutive_losses,
            "max_consecutive_losses": s.max_consecutive_losses,
            "worst_return": s.worst_return,
            "best_return": s.best_return,
            "capital_requirements": s.capital_requirements,
        }

    @staticmethod
    def _ruin_interpretation(prob: float) -> str:
        if prob < 0.01:
            return "Very Low — extremely unlikely"
        if prob < 0.05:
            return "Low — uncommon scenario"
        if prob < 0.10:
            return "Moderate — worth monitoring"
        if prob < 0.25:
            return "Elevated — reduce position sizes"
        return "High — strategy requires review"

    @staticmethod
    def _risk_rating(ruin: dict[str, float]) -> str:
        """Simple composite risk rating from the 50%-ruin probability."""
        p50 = ruin.get("50pct", 0.0)
        if p50 < 0.01:
            return "LOW"
        if p50 < 0.05:
            return "MODERATE"
        if p50 < 0.15:
            return "ELEVATED"
        return "HIGH"

    @staticmethod
    def _capital_recommendation(
        current_capital: float, cap_reqs: dict[str, float]
    ) -> str:
        """Plain-text recommendation based on capital adequacy."""
        # Use 50%-threshold requirement as the primary metric
        req_50 = cap_reqs.get("50pct", 0.0)
        if req_50 == 0:
            return "Insufficient simulation data to make a recommendation."
        if current_capital >= req_50:
            surplus_pct = round((current_capital - req_50) / req_50 * 100, 1)
            return (
                f"Capital is sufficient. You have {surplus_pct}% margin above "
                f"the minimum required (₹{req_50:,.0f}) to survive the p95 "
                f"worst-case drawdown without losing more than 50% of capital."
            )
        deficit = round(req_50 - current_capital, 2)
        return (
            f"Capital may be insufficient. You need approximately ₹{deficit:,.0f} "
            f"more (total ₹{req_50:,.0f}) to ensure the p95 worst-case drawdown "
            f"does not cause a 50%+ loss. Consider reducing position sizes or "
            f"adding capital."
        )
