"""
Backtest analytics service.

Generates deep analytical insights from a completed backtest run by
querying BacktestTrade records and performing post-hoc analysis.

Analytics provided:
  - Best/worst performing symbols (by total P&L and by win rate)
  - Best breakout entry times (which 15-min slot produces most winners)
  - Probability threshold sensitivity (what if threshold was 60%/75%/80%?)
  - SL range analysis (P&L vs ORB range width)
  - Long vs Short performance split
  - Monthly performance heatmap data

Architecture:
  - Service calls BacktestTradeRepository only — never Beanie directly.
  - No BacktestEngine calls — analytics derive from already-stored trade docs.
  - All computation is synchronous Python; no thread-pool needed (fast aggregation).
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timezone
from typing import Optional

import pytz

from app.core.exceptions import BacktestNotFoundException
from app.models.backtest_trade import ExitReason, TradeSide
from app.repositories.backtest_metrics_repository import BacktestMetricsRepository
from app.repositories.backtest_run_repository import BacktestRunRepository
from app.repositories.backtest_trade_repository import BacktestTradeRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")


@dataclass
class SymbolPerformance:
    symbol: str
    total_trades: int
    wins: int
    losses: int
    total_pnl: float
    win_rate: float
    avg_pnl: float
    best_trade: float
    worst_trade: float


@dataclass
class EntryTimeSlot:
    """Performance summary for a particular 15-min entry time slot (IST)."""
    time_ist: str          # e.g. "09:30", "09:45", …
    total_entries: int
    wins: int
    win_rate: float
    avg_pnl: float
    total_pnl: float


@dataclass
class BacktestAnalyticsResult:
    run_id: str
    best_symbols: list[SymbolPerformance] = field(default_factory=list)
    worst_symbols: list[SymbolPerformance] = field(default_factory=list)
    entry_time_analysis: list[EntryTimeSlot] = field(default_factory=list)
    long_metrics: dict = field(default_factory=dict)
    short_metrics: dict = field(default_factory=dict)
    monthly_pnl_heatmap: dict = field(default_factory=dict)  # {month: {symbol: pnl}}
    orb_range_buckets: list[dict] = field(default_factory=list)
    probability_sensitivity: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


class BacktestAnalyticsService:
    """
    Generates post-run analytics for a completed backtest.

    Usage:
        svc = BacktestAnalyticsService()
        analytics = await svc.generate_analytics(run_id="...")
    """

    def __init__(self) -> None:
        self._run_repo     = BacktestRunRepository()
        self._trade_repo   = BacktestTradeRepository()
        self._metrics_repo = BacktestMetricsRepository()

    async def generate_analytics(self, run_id: str) -> BacktestAnalyticsResult:
        """
        Generate all analytics for a completed backtest run.

        Fetches all executed trades into memory and performs aggregations.
        """
        # Validate run exists
        run = await self._run_repo.get_by_run_id(run_id)
        if run is None:
            raise BacktestNotFoundException(run_id)

        # Load only executed trades (skip NO_BREAKOUT)
        all_trades = await self._trade_repo.get_all_trades_for_run(run_id)
        executed = [t for t in all_trades if t.exit_reason != ExitReason.NO_BREAKOUT]

        analytics = BacktestAnalyticsResult(run_id=run_id)

        if not executed:
            logger.warning("[%s] No executed trades found for analytics.", run_id)
            return analytics

        # Run all analyses
        symbol_perf = self._analyse_symbols(executed)
        analytics.best_symbols  = sorted(symbol_perf, key=lambda x: x.total_pnl, reverse=True)[:10]
        analytics.worst_symbols = sorted(symbol_perf, key=lambda x: x.total_pnl)[:10]
        analytics.entry_time_analysis = self._analyse_entry_times(executed)
        analytics.long_metrics, analytics.short_metrics = self._analyse_direction_split(executed)
        analytics.monthly_pnl_heatmap = self._build_monthly_heatmap(executed)
        analytics.orb_range_buckets = self._analyse_orb_range(executed)
        analytics.probability_sensitivity = self._analyse_probability_sensitivity(all_trades)

        analytics.metadata = {
            "total_executed_trades": len(executed),
            "total_no_breakout": len(all_trades) - len(executed),
            "symbols_analysed": len(set(t.symbol for t in executed)),
        }

        logger.info(
            "[%s] Analytics generated: %d trades, %d symbols",
            run_id, len(executed), analytics.metadata["symbols_analysed"],
        )
        return analytics

    # ── Per-symbol analysis ───────────────────────────────────────────────────

    @staticmethod
    def _analyse_symbols(
        executed: list,
    ) -> list[SymbolPerformance]:
        """Compute per-symbol performance from executed trade list."""
        by_symbol: dict[str, list] = defaultdict(list)
        for trade in executed:
            by_symbol[trade.symbol].append(trade)

        result = []
        for symbol, trades in by_symbol.items():
            wins = [t for t in trades if t.pnl > 0]
            pnl_list = [t.pnl for t in trades]
            result.append(SymbolPerformance(
                symbol=symbol,
                total_trades=len(trades),
                wins=len(wins),
                losses=len(trades) - len(wins),
                total_pnl=round(sum(pnl_list), 2),
                win_rate=round(len(wins) / len(trades), 4),
                avg_pnl=round(sum(pnl_list) / len(trades), 2),
                best_trade=round(max(pnl_list), 2),
                worst_trade=round(min(pnl_list), 2),
            ))
        return result

    # ── Entry time analysis ───────────────────────────────────────────────────

    @staticmethod
    def _analyse_entry_times(executed: list) -> list[EntryTimeSlot]:
        """
        Group trades by entry candle time slot (IST HH:MM) and compute performance.

        Reveals which 15-min window produces the best win rate and P&L.
        """
        by_slot: dict[str, list] = defaultdict(list)
        for trade in executed:
            if trade.entry_time is None:
                continue
            slot = trade.entry_time.astimezone(IST).strftime("%H:%M")
            by_slot[slot].append(trade)

        result = []
        for slot, trades in sorted(by_slot.items()):
            wins = [t for t in trades if t.pnl > 0]
            pnl_list = [t.pnl for t in trades]
            result.append(EntryTimeSlot(
                time_ist=slot,
                total_entries=len(trades),
                wins=len(wins),
                win_rate=round(len(wins) / len(trades), 4),
                avg_pnl=round(sum(pnl_list) / len(trades), 2),
                total_pnl=round(sum(pnl_list), 2),
            ))
        return result

    # ── Long vs Short split ───────────────────────────────────────────────────

    @staticmethod
    def _analyse_direction_split(executed: list) -> tuple[dict, dict]:
        """Split trade metrics by LONG vs SHORT direction."""
        long_trades  = [t for t in executed if t.trade_side == TradeSide.LONG]
        short_trades = [t for t in executed if t.trade_side == TradeSide.SHORT]

        def _summary(trades: list) -> dict:
            if not trades:
                return {"total": 0, "wins": 0, "win_rate": 0.0, "total_pnl": 0.0}
            wins = [t for t in trades if t.pnl > 0]
            pnl_list = [t.pnl for t in trades]
            return {
                "total": len(trades),
                "wins": len(wins),
                "losses": len(trades) - len(wins),
                "win_rate": round(len(wins) / len(trades), 4),
                "total_pnl": round(sum(pnl_list), 2),
                "avg_pnl": round(sum(pnl_list) / len(trades), 2),
            }

        return _summary(long_trades), _summary(short_trades)

    # ── Monthly P&L heatmap ───────────────────────────────────────────────────

    @staticmethod
    def _build_monthly_heatmap(executed: list) -> dict:
        """
        Build {month_key: {symbol: pnl}} for a calendar-grid P&L heatmap.

        Month key format: "YYYY-MM".
        """
        heatmap: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for trade in executed:
            if trade.entry_time is None:
                continue
            month_key = trade.entry_time.astimezone(IST).strftime("%Y-%m")
            heatmap[month_key][trade.symbol] += trade.pnl

        return {
            month: {sym: round(pnl, 2) for sym, pnl in syms.items()}
            for month, syms in sorted(heatmap.items())
        }

    # ── ORB range analysis ────────────────────────────────────────────────────

    @staticmethod
    def _analyse_orb_range(executed: list) -> list[dict]:
        """
        Bucket trades by ORB range width and compare P&L across buckets.

        Reveals whether tighter ORBs produce better results.
        ORB range buckets (% of orb_low): <0.3%, 0.3-0.5%, 0.5-0.75%, 0.75-1.0%
        """
        buckets = [
            {"label": "< 0.30%",   "min": 0.0,  "max": 0.30},
            {"label": "0.30–0.50%","min": 0.30, "max": 0.50},
            {"label": "0.50–0.75%","min": 0.50, "max": 0.75},
            {"label": "0.75–1.00%","min": 0.75, "max": 1.00},
            {"label": "> 1.00%",   "min": 1.00, "max": 999.0},
        ]

        bucket_trades: dict[str, list] = {b["label"]: [] for b in buckets}
        for trade in executed:
            if trade.orb_low <= 0:
                continue
            orb_pct = (trade.orb_high - trade.orb_low) / trade.orb_low * 100
            for b in buckets:
                if b["min"] <= orb_pct < b["max"]:
                    bucket_trades[b["label"]].append(trade)
                    break

        result = []
        for b in buckets:
            trades = bucket_trades[b["label"]]
            if not trades:
                result.append({
                    "label": b["label"], "total": 0,
                    "wins": 0, "win_rate": 0.0, "total_pnl": 0.0, "avg_pnl": 0.0,
                })
                continue
            wins = [t for t in trades if t.pnl > 0]
            pnl_list = [t.pnl for t in trades]
            result.append({
                "label": b["label"],
                "total": len(trades),
                "wins": len(wins),
                "win_rate": round(len(wins) / len(trades), 4),
                "total_pnl": round(sum(pnl_list), 2),
                "avg_pnl": round(sum(pnl_list) / len(trades), 2),
            })
        return result

    # ── Probability threshold sensitivity ────────────────────────────────────

    @staticmethod
    def _analyse_probability_sensitivity(all_trades: list) -> list[dict]:
        """
        Show what would happen if we used different probability thresholds.

        Simulates post-hoc filtering at 0.60, 0.65, 0.70, 0.75, 0.80.
        Excludes NO_BREAKOUT trades (probability filter doesn't affect them
        in this post-hoc analysis since we're just filtering by prob_score).
        """
        thresholds = [0.60, 0.65, 0.70, 0.75, 0.80]
        executed = [t for t in all_trades if t.exit_reason != ExitReason.NO_BREAKOUT]

        result = []
        for threshold in thresholds:
            filtered = [t for t in executed if t.probability_score >= threshold]
            if not filtered:
                result.append({
                    "threshold": threshold, "total_trades": 0,
                    "wins": 0, "win_rate": 0.0, "total_pnl": 0.0, "avg_pnl": 0.0,
                })
                continue
            wins = [t for t in filtered if t.pnl > 0]
            pnl_list = [t.pnl for t in filtered]
            result.append({
                "threshold": threshold,
                "total_trades": len(filtered),
                "wins": len(wins),
                "win_rate": round(len(wins) / len(filtered), 4),
                "total_pnl": round(sum(pnl_list), 2),
                "avg_pnl": round(sum(pnl_list) / len(filtered), 2),
            })
        return result
