"""
Research report generator.

Pure Python — NO database calls, NO I/O.
Aggregates all analytics engine results into JSON-ready report structures.

Report sections:
  1. executive_summary      — high-level pass/fail verdict on the strategy
  2. parameter_sensitivity  — ranked results for each parameter sweep
  3. stock_rankings         — tradable vs avoid lists
  4. time_edge              — best/worst entry windows
  5. market_conditions      — performance by day type
  6. failure_diagnostics    — SL patterns, fake breakouts, risky stocks
  7. recommendations        — actionable insights derived from all sections

All output is plain Python dicts — serialisable to JSON without transformation.
The ResearchService passes these dicts into ResearchRun.metadata and the
/research/reports/{run_id} API endpoint returns them directly.
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from app.research.failure_analytics import FailureAnalyticsResult
from app.research.market_condition_analytics import MarketConditionResult
from app.research.parameter_optimizer import SweepResult
from app.research.stock_analytics import StockAnalyticsResult
from app.research.time_analytics import TimeAnalyticsResult
from app.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ResearchReport:
    """
    Complete research report — output of ReportGenerator.generate().

    All fields are plain Python dicts for direct JSON serialisation.
    """

    run_id: str

    executive_summary: dict = field(default_factory=dict)
    parameter_sensitivity: dict = field(default_factory=dict)
    stock_rankings: dict = field(default_factory=dict)
    time_edge: dict = field(default_factory=dict)
    market_conditions: dict = field(default_factory=dict)
    failure_diagnostics: dict = field(default_factory=dict)
    recommendations: list[str] = field(default_factory=list)

    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Return a fully serialisable dict of the entire report."""
        return {
            "run_id": self.run_id,
            "executive_summary": self.executive_summary,
            "parameter_sensitivity": self.parameter_sensitivity,
            "stock_rankings": self.stock_rankings,
            "time_edge": self.time_edge,
            "market_conditions": self.market_conditions,
            "failure_diagnostics": self.failure_diagnostics,
            "recommendations": self.recommendations,
            "metadata": self.metadata,
        }


class ReportGenerator:
    """
    Assembles a ResearchReport from analytics engine outputs.

    Usage:
        generator = ReportGenerator()
        report = generator.generate(
            run_id="...",
            sweep_result=sweep_result,
            stock_result=stock_result,
            time_result=time_result,
            market_result=market_result,
            failure_result=failure_result,
        )
    """

    def generate(
        self,
        run_id: str,
        sweep_result: Optional[SweepResult] = None,
        stock_result: Optional[StockAnalyticsResult] = None,
        time_result: Optional[TimeAnalyticsResult] = None,
        market_result: Optional[MarketConditionResult] = None,
        failure_result: Optional[FailureAnalyticsResult] = None,
    ) -> ResearchReport:
        """
        Build the full research report.

        All inputs are optional — sections will be empty dicts if not provided,
        allowing partial reports when some analyses were skipped.
        """
        report = ResearchReport(run_id=run_id)

        report.parameter_sensitivity = self._build_parameter_sensitivity(sweep_result)
        report.stock_rankings        = self._build_stock_rankings(stock_result)
        report.time_edge             = self._build_time_edge(time_result)
        report.market_conditions     = self._build_market_conditions(market_result)
        report.failure_diagnostics   = self._build_failure_diagnostics(failure_result)
        report.executive_summary     = self._build_executive_summary(
            sweep_result, stock_result, time_result, market_result, failure_result
        )
        report.recommendations       = self._generate_recommendations(
            sweep_result, stock_result, time_result, market_result, failure_result
        )

        report.metadata = {
            "run_id": run_id,
            "sections_generated": [
                s for s, v in [
                    ("parameter_sensitivity", sweep_result),
                    ("stock_rankings",        stock_result),
                    ("time_edge",             time_result),
                    ("market_conditions",     market_result),
                    ("failure_diagnostics",   failure_result),
                ]
                if v is not None
            ],
        }

        logger.info(
            "[%s] ReportGenerator: report built with %d recommendations.",
            run_id,
            len(report.recommendations),
        )
        return report

    # ── Section builders ──────────────────────────────────────────────────────

    @staticmethod
    def _build_parameter_sensitivity(sweep: Optional[SweepResult]) -> dict:
        """Build ranked parameter sensitivity tables."""
        if sweep is None:
            return {}

        # Group points by parameter_name
        by_param: dict[str, list] = {}
        for point in sweep.points:
            by_param.setdefault(point.parameter_name, []).append(point)

        result: dict[str, Any] = {}
        for param_name, points in sorted(by_param.items()):
            # Sort by total_pnl descending for the ranking table
            ranked = sorted(points, key=lambda p: p.metrics.total_pnl, reverse=True)
            result[param_name] = {
                "best_value": ranked[0].parameter_value if ranked else None,
                "ranking": [
                    {
                        "value": p.parameter_value,
                        "total_trades": p.metrics.total_trades,
                        "win_rate": round(p.metrics.win_rate * 100, 2),
                        "total_pnl": p.metrics.total_pnl,
                        "expectancy": p.metrics.expectancy,
                        "profit_factor": p.metrics.profit_factor,
                        "max_drawdown": p.metrics.max_drawdown,
                        "sharpe_ratio": p.metrics.sharpe_ratio,
                        "sl_hit_rate": round(p.metrics.sl_hit_rate * 100, 2),
                    }
                    for p in ranked
                ],
            }

        result["summary"] = {
            "total_configs_tested": sweep.total_configs_run,
            "failed_configs": sweep.failed_configs,
        }
        return result

    @staticmethod
    def _build_stock_rankings(stock: Optional[StockAnalyticsResult]) -> dict:
        """Build stock tradability rankings."""
        if stock is None:
            return {}

        def _sym_dict(a) -> dict:
            return {
                "symbol": a.symbol,
                "total_trades": a.total_trades,
                "win_rate": round(a.win_rate * 100, 2),
                "sl_hit_rate": round(a.sl_hit_rate * 100, 2),
                "total_pnl": a.total_pnl,
                "avg_pnl": a.avg_pnl,
                "expectancy": a.expectancy,
                "profit_factor": a.profit_factor,
                "breakout_success_rate": round(a.breakout_success_rate * 100, 2),
                "best_time_range": a.best_breakout_time_range,
                "tradability_score": a.tradability_score,
            }

        return {
            "top_performers": [_sym_dict(a) for a in stock.top_performers],
            "worst_performers": [_sym_dict(a) for a in stock.worst_performers],
            "high_sl_risk": [_sym_dict(a) for a in stock.high_sl_risk],
            "metadata": stock.metadata,
        }

    @staticmethod
    def _build_time_edge(time: Optional[TimeAnalyticsResult]) -> dict:
        """Build time-of-day performance breakdown."""
        if time is None:
            return {}

        def _bucket_dict(b) -> dict:
            return {
                "label": b.label,
                "total_entries": b.total_entries,
                "win_rate": round(b.win_rate * 100, 2),
                "sl_hit_rate": round(b.sl_hit_rate * 100, 2),
                "avg_pnl": b.avg_pnl,
                "total_pnl": b.total_pnl,
                "long_win_rate": round(b.long_win_rate * 100, 2),
                "short_win_rate": round(b.short_win_rate * 100, 2),
                "avg_risk_reward": b.avg_risk_reward,
            }

        return {
            "buckets": [_bucket_dict(b) for b in time.buckets],
            "best_bucket": time.best_bucket,
            "worst_bucket": time.worst_bucket,
            "win_rate_trend": time.win_rate_trend,
            "best_long_bucket": time.best_long_bucket,
            "best_short_bucket": time.best_short_bucket,
            "metadata": time.metadata,
        }

    @staticmethod
    def _build_market_conditions(market: Optional[MarketConditionResult]) -> dict:
        """Build market condition performance breakdown."""
        if market is None:
            return {}

        def _cond_dict(c) -> dict:
            return {
                "condition": c.condition,
                "total_days": c.total_days,
                "total_trades": c.total_trades,
                "win_rate": round(c.win_rate * 100, 2),
                "sl_hit_rate": round(c.sl_hit_rate * 100, 2),
                "total_pnl": c.total_pnl,
                "avg_daily_pnl": c.avg_daily_pnl,
                "avg_trades_per_day": c.avg_trades_per_day,
            }

        return {
            "conditions": [_cond_dict(c) for c in market.condition_stats],
            "best_condition": market.best_condition,
            "worst_condition": market.worst_condition,
            "metadata": market.metadata,
        }

    @staticmethod
    def _build_failure_diagnostics(failure: Optional[FailureAnalyticsResult]) -> dict:
        """Build failure pattern diagnostic report."""
        if failure is None:
            return {}

        return {
            "overall": {
                "total_executed": failure.total_executed,
                "total_sl_hits": failure.total_sl_hits,
                "sl_hit_rate_pct": round(failure.overall_sl_hit_rate * 100, 2),
                "avg_loss_on_sl": failure.avg_loss_on_sl,
            },
            "sl_timing": [
                {
                    "label": s.label,
                    "sl_count": s.sl_count,
                    "pct_of_all_sl": s.pct_of_all_sl,
                    "avg_loss": s.avg_loss,
                }
                for s in failure.sl_timing_distribution
            ],
            "peak_sl_time_bucket": failure.peak_sl_time_bucket,
            "fake_breakouts": {
                "count": failure.fake_breakout_count,
                "rate_pct": round(failure.fake_breakout_rate * 100, 2),
                "examples": [
                    {
                        "symbol": fb.symbol,
                        "date": fb.date_str,
                        "side": fb.trade_side,
                        "orb_range_pct": fb.orb_range_pct,
                        "pnl": fb.pnl,
                        "minutes_held": fb.minutes_held,
                    }
                    for fb in failure.fake_breakouts[:20]   # cap for readability
                ],
            },
            "opposite_side_violations": {
                "count": failure.opposite_side_violation_count,
                "rate_pct": round(failure.opposite_side_violation_rate * 100, 2),
            },
            "high_risk_symbols": [
                {
                    "symbol": s.symbol,
                    "total_trades": s.total_trades,
                    "sl_hits": s.sl_hits,
                    "sl_hit_rate_pct": round(s.sl_hit_rate * 100, 2),
                    "avg_loss_when_sl": s.avg_loss_when_sl,
                }
                for s in failure.high_risk_symbols
            ],
            "choppy_days": {
                "count": len(failure.choppy_days),
                "dates": failure.choppy_days,
                "avg_no_breakout_rate_pct": round(failure.avg_no_breakout_rate * 100, 2),
            },
            "sl_cluster_days": {
                "count": len(failure.sl_cluster_days),
                "dates": failure.sl_cluster_days,
                "max_sl_hits_single_day": failure.max_sl_hits_single_day,
            },
            "metadata": failure.metadata,
        }

    # ── Executive summary ─────────────────────────────────────────────────────

    @staticmethod
    def _build_executive_summary(
        sweep: Optional[SweepResult],
        stock: Optional[StockAnalyticsResult],
        time: Optional[TimeAnalyticsResult],
        market: Optional[MarketConditionResult],
        failure: Optional[FailureAnalyticsResult],
    ) -> dict:
        """
        Build a concise top-level summary highlighting the most important findings.
        """
        summary: dict[str, Any] = {"strategy": "One-Side ORB"}

        # Best parameter configuration (from probability_threshold sweep, highest pnl)
        if sweep and sweep.points:
            prob_points = [p for p in sweep.points if p.parameter_name == "probability_threshold"]
            if prob_points:
                best_prob = max(prob_points, key=lambda p: p.metrics.total_pnl)
                summary["best_probability_threshold"] = {
                    "value": best_prob.parameter_value,
                    "win_rate_pct": round(best_prob.metrics.win_rate * 100, 2),
                    "total_pnl": best_prob.metrics.total_pnl,
                }

            orb_points = [p for p in sweep.points if p.parameter_name == "max_orb_range_pct"]
            if orb_points:
                best_orb = max(orb_points, key=lambda p: p.metrics.total_pnl)
                summary["best_orb_range_filter"] = {
                    "value": best_orb.parameter_value,
                    "win_rate_pct": round(best_orb.metrics.win_rate * 100, 2),
                    "total_pnl": best_orb.metrics.total_pnl,
                }

        if stock and stock.top_performers:
            summary["top_3_stocks"] = [a.symbol for a in stock.top_performers[:3]]
        if stock and stock.worst_performers:
            summary["bottom_3_stocks"] = [a.symbol for a in stock.worst_performers[:3]]

        if time:
            summary["best_entry_window"] = time.best_bucket
            summary["win_rate_trend"]    = time.win_rate_trend

        if market:
            summary["best_market_condition"]  = market.best_condition
            summary["worst_market_condition"] = market.worst_condition

        if failure:
            summary["overall_sl_hit_rate_pct"] = round(failure.overall_sl_hit_rate * 100, 2)
            summary["peak_sl_time"]            = failure.peak_sl_time_bucket
            summary["high_risk_symbol_count"]  = len(failure.high_risk_symbols)
            summary["choppy_day_count"]        = len(failure.choppy_days)

        return summary

    # ── Recommendations ───────────────────────────────────────────────────────

    @staticmethod
    def _generate_recommendations(
        sweep: Optional[SweepResult],
        stock: Optional[StockAnalyticsResult],
        time: Optional[TimeAnalyticsResult],
        market: Optional[MarketConditionResult],
        failure: Optional[FailureAnalyticsResult],
    ) -> list[str]:
        """
        Generate actionable, specific recommendations based on analytics findings.

        Each recommendation is a plain English sentence that a trader can act on.
        """
        recs: list[str] = []

        # Parameter recommendations
        if sweep and sweep.points:
            prob_points = [p for p in sweep.points if p.parameter_name == "probability_threshold"]
            if prob_points:
                best_prob = max(prob_points, key=lambda p: p.metrics.total_pnl)
                recs.append(
                    f"Set probability_threshold to {best_prob.parameter_value} — "
                    f"highest P&L across the sweep (₹{best_prob.metrics.total_pnl:,.0f})."
                )

            orb_points = [p for p in sweep.points if p.parameter_name == "max_orb_range_pct"]
            if orb_points:
                best_orb = max(orb_points, key=lambda p: p.metrics.total_pnl)
                recs.append(
                    f"Use max_orb_range_pct={best_orb.parameter_value}% — "
                    f"optimal ORB width filter for this data range."
                )

            time_points = [p for p in sweep.points if p.parameter_name == "max_entry_time_ist"]
            if time_points:
                best_time = max(time_points, key=lambda p: p.metrics.win_rate)
                recs.append(
                    f"Cut entry window at {best_time.parameter_value} IST — "
                    f"win rate peaks at {best_time.metrics.win_rate * 100:.1f}%."
                )

        # Stock recommendations
        if stock:
            avoid = [a for a in stock.high_sl_risk if a.sl_hit_rate > 0.6]
            if avoid:
                symbols_str = ", ".join(a.symbol for a in avoid[:5])
                recs.append(
                    f"Consider excluding {symbols_str} — SL hit rate >60% indicates "
                    "these stocks frequently trap traders."
                )
            if stock.top_performers:
                top_syms = ", ".join(a.symbol for a in stock.top_performers[:5])
                recs.append(
                    f"Prioritise {top_syms} — highest tradability scores in the universe."
                )

        # Time recommendations
        if time and time.best_bucket:
            recs.append(
                f"The {time.best_bucket} IST window shows the strongest edge — "
                "consider allocating more capital to early entries."
            )
            if time.win_rate_trend == "declining":
                recs.append(
                    "Win rate declines through the session. Consider a tighter "
                    "entry cutoff to avoid late-day deterioration."
                )
            elif time.win_rate_trend == "improving":
                recs.append(
                    "Win rate improves through the session — late entries may "
                    "carry less risk than they appear. Do not cut too early."
                )

        # Market condition recommendations
        if market:
            if market.worst_condition:
                recs.append(
                    f"Consider sitting out on '{market.worst_condition}' days — "
                    "strategy edge disappears under this market character."
                )
            if market.best_condition:
                recs.append(
                    f"Strategy excels on '{market.best_condition}' days — "
                    "size up or expand the universe on these conditions."
                )

        # Failure recommendations
        if failure:
            if failure.overall_sl_hit_rate > 0.45:
                recs.append(
                    f"SL hit rate of {failure.overall_sl_hit_rate * 100:.1f}% is elevated. "
                    "Consider widening sl_buffer_pct or tightening ORB range filter."
                )
            if len(failure.sl_cluster_days) > 5:
                recs.append(
                    f"{len(failure.sl_cluster_days)} days had 3+ simultaneous SL hits — "
                    "implement a daily max-loss limit (circuit-breaker) to cap cluster-risk."
                )
            if len(failure.choppy_days) > 10:
                recs.append(
                    f"{len(failure.choppy_days)} high-NO_BREAKOUT days detected. "
                    "Add a pre-market VIX or gap-size filter to skip choppy sessions."
                )
            if failure.peak_sl_time_bucket:
                recs.append(
                    f"Most SL hits occur in the {failure.peak_sl_time_bucket} window. "
                    "Consider tightening the time filter or adding a trailing stop."
                )

        if not recs:
            recs.append("Insufficient data for recommendations — run a longer backtest period.")

        return recs
