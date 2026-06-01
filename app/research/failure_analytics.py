"""
Failure analytics engine — critical for improving strategy survivability.

Pure Python — NO database calls, NO I/O.
Analyses all the ways the strategy FAILS: stop-loss patterns, fake breakouts,
opposite-side violations, choppy-day behavior, and high-risk stock profiles.

This engine is the most important for production-grade strategy improvement.
Understanding failure modes is worth more than optimising for wins.

Analyses performed:
  1. SL timing distribution  — when in the session do stops get hit most?
  2. Fake breakout detection — trades where the opposite side was also touched post-entry
  3. Opposite-side violation — candidate days where both ORB sides were breached
  4. Common failure patterns — co-occurrence of failure conditions
  5. High-risk symbol profiles — stocks with sl_hit_rate > threshold
  6. Choppy-day behavior     — days with high NO_BREAKOUT rates
  7. SL-hit clustering       — are SL hits correlated (systematic risk days)?
"""

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pytz

from app.models.backtest_trade import ExitReason, TradeSide
from app.utils.logger import get_logger

logger = get_logger(__name__)

IST = pytz.timezone("Asia/Kolkata")

_HIGH_RISK_SL_THRESHOLD = 0.55     # symbols with sl_hit_rate above this are flagged
_MIN_TRADES_FOR_ANALYSIS = 5       # minimum trades before including in rankings


@dataclass
class SlTimingSlot:
    """SL hit distribution by IST time bucket."""

    label: str
    sl_count: int = 0
    pct_of_all_sl: float = 0.0
    avg_loss: float = 0.0


@dataclass
class FakeBreakoutProfile:
    """A trade where the ORB was broken but the stop was then hit — fake breakout."""

    symbol: str
    date_str: str
    trade_side: str
    orb_range_pct: float
    pnl: float
    minutes_held: Optional[float]


@dataclass
class HighRiskSymbol:
    """Symbol with an elevated SL hit rate."""

    symbol: str
    total_trades: int
    sl_hits: int
    sl_hit_rate: float
    avg_loss_when_sl: float


@dataclass
class FailureAnalyticsResult:
    """
    Output of FailureAnalyticsEngine.analyse().

    All fields are populated even if empty (never None lists).
    """

    # ── SL timing ─────────────────────────────────────────────────────────────
    sl_timing_distribution: list[SlTimingSlot] = field(default_factory=list)
    peak_sl_time_bucket: Optional[str] = None

    # ── Fake breakout analysis ─────────────────────────────────────────────────
    fake_breakout_count: int = 0
    fake_breakout_rate: float = 0.0      # as % of executed trades
    fake_breakouts: list[FakeBreakoutProfile] = field(default_factory=list)

    # ── Opposite-side violations ───────────────────────────────────────────────
    opposite_side_violation_count: int = 0
    opposite_side_violation_rate: float = 0.0

    # ── High-risk symbols ──────────────────────────────────────────────────────
    high_risk_symbols: list[HighRiskSymbol] = field(default_factory=list)

    # ── Choppy-day behavior ────────────────────────────────────────────────────
    choppy_days: list[str] = field(default_factory=list)   # date strings
    avg_no_breakout_rate: float = 0.0   # on "choppy" days

    # ── SL clustering ─────────────────────────────────────────────────────────
    sl_cluster_days: list[str] = field(default_factory=list)  # days with ≥3 SL hits
    max_sl_hits_single_day: int = 0

    # ── Overall failure summary ────────────────────────────────────────────────
    total_executed: int = 0
    total_sl_hits: int = 0
    overall_sl_hit_rate: float = 0.0
    avg_loss_on_sl: float = 0.0

    metadata: dict = field(default_factory=dict)


class FailureAnalyticsEngine:
    """
    Deep analysis of strategy failure patterns.

    Usage:
        engine = FailureAnalyticsEngine()
        result = engine.analyse(all_trades)   # include NO_BREAKOUT trades
    """

    def analyse(self, trades: list) -> FailureAnalyticsResult:
        """
        Run all failure analyses on the full trade list.

        Args:
            trades: ALL BacktestTrade records including NO_BREAKOUT trades.

        Returns:
            FailureAnalyticsResult with all failure diagnostics populated.
        """
        result = FailureAnalyticsResult()

        executed    = [t for t in trades if t.exit_reason != ExitReason.NO_BREAKOUT]
        no_breakout = [t for t in trades if t.exit_reason == ExitReason.NO_BREAKOUT]
        sl_hits     = [t for t in executed if t.exit_reason == ExitReason.SL_HIT]

        result.total_executed      = len(executed)
        result.total_sl_hits       = len(sl_hits)
        result.overall_sl_hit_rate = round(len(sl_hits) / len(executed), 4) if executed else 0.0

        if sl_hits:
            result.avg_loss_on_sl = round(statistics.mean(t.pnl for t in sl_hits), 2)

        # Run all sub-analyses
        result.sl_timing_distribution, result.peak_sl_time_bucket = (
            self._analyse_sl_timing(sl_hits)
        )
        result.fake_breakouts, result.fake_breakout_count, result.fake_breakout_rate = (
            self._detect_fake_breakouts(executed)
        )
        result.opposite_side_violation_count, result.opposite_side_violation_rate = (
            self._count_opposite_side_violations(executed)
        )
        result.high_risk_symbols = self._identify_high_risk_symbols(executed)
        result.choppy_days, result.avg_no_breakout_rate = (
            self._analyse_choppy_days(trades)
        )
        result.sl_cluster_days, result.max_sl_hits_single_day = (
            self._find_sl_cluster_days(sl_hits)
        )

        result.metadata = {
            "total_trades": len(trades),
            "total_executed": len(executed),
            "total_no_breakout": len(no_breakout),
            "total_sl_hits": len(sl_hits),
        }

        logger.info(
            "FailureAnalyticsEngine: %d executed trades, %d SL hits (%.1f%%), "
            "%d fake breakouts, %d high-risk symbols, %d cluster days.",
            len(executed),
            len(sl_hits),
            result.overall_sl_hit_rate * 100,
            result.fake_breakout_count,
            len(result.high_risk_symbols),
            len(result.sl_cluster_days),
        )
        return result

    # ── SL timing ─────────────────────────────────────────────────────────────

    @staticmethod
    def _analyse_sl_timing(
        sl_hits: list,
    ) -> tuple[list[SlTimingSlot], Optional[str]]:
        """Distribute SL hits across IST time buckets to reveal when stops run most."""
        buckets = [
            ("09:30–10:00", 4 * 60,       4 * 60 + 29),
            ("10:00–10:30", 4 * 60 + 30,  4 * 60 + 59),
            ("10:30–11:00", 5 * 60,       5 * 60 + 29),
            ("11:00–11:30", 5 * 60 + 30,  5 * 60 + 59),
            ("11:30–15:15", 6 * 60,       9 * 60 + 45),   # late + EOD
        ]
        bucket_trades: dict[str, list] = {b[0]: [] for b in buckets}

        for trade in sl_hits:
            if trade.exit_time is None:
                continue
            utc_min = trade.exit_time.hour * 60 + trade.exit_time.minute
            for label, start, end in buckets:
                if start <= utc_min <= end:
                    bucket_trades[label].append(trade)
                    break

        total_sl = len(sl_hits)
        slots: list[SlTimingSlot] = []
        for label, _, _ in buckets:
            bt = bucket_trades[label]
            slot = SlTimingSlot(label=label, sl_count=len(bt))
            if total_sl > 0:
                slot.pct_of_all_sl = round(len(bt) / total_sl * 100, 2)
            if bt:
                slot.avg_loss = round(statistics.mean(t.pnl for t in bt), 2)
            slots.append(slot)

        peak = max(slots, key=lambda s: s.sl_count, default=None)
        peak_label = peak.label if peak and peak.sl_count > 0 else None

        return slots, peak_label

    # ── Fake breakout detection ────────────────────────────────────────────────

    @staticmethod
    def _detect_fake_breakouts(
        executed: list,
    ) -> tuple[list[FakeBreakoutProfile], int, float]:
        """
        A "fake breakout" is any trade that entered AND had its stop hit.

        The stop-hit itself signals the breakout direction was wrong.
        We additionally flag the ORB range to see if tight ORBs are more prone.
        """
        fakes: list[FakeBreakoutProfile] = []

        for trade in executed:
            if trade.exit_reason != ExitReason.SL_HIT:
                continue

            orb_range_pct = 0.0
            if trade.orb_low > 0:
                orb_range_pct = round(
                    (trade.orb_high - trade.orb_low) / trade.orb_low * 100, 4
                )

            minutes_held: Optional[float] = None
            if trade.entry_time and trade.exit_time:
                delta = (trade.exit_time - trade.entry_time).total_seconds()
                minutes_held = round(delta / 60, 1)

            date_str = ""
            if trade.entry_time:
                date_str = trade.entry_time.astimezone(IST).date().isoformat()

            fakes.append(FakeBreakoutProfile(
                symbol=trade.symbol,
                date_str=date_str,
                trade_side=trade.trade_side.value if hasattr(trade.trade_side, "value") else str(trade.trade_side),
                orb_range_pct=orb_range_pct,
                pnl=trade.pnl,
                minutes_held=minutes_held,
            ))

        fake_count = len(fakes)
        fake_rate  = round(fake_count / len(executed), 4) if executed else 0.0
        return fakes, fake_count, fake_rate

    # ── Opposite-side violations ───────────────────────────────────────────────

    @staticmethod
    def _count_opposite_side_violations(executed: list) -> tuple[int, float]:
        """
        Count trades where post-entry price hit BOTH ORB high AND ORB low,
        indicating a two-sided / choppy day.

        Uses the orb_high/orb_low from the trade record and checks whether
        the adverse side was reached (SL hit → opposite side was definitely touched).
        Trades are long: stop = orb_low side; short: stop = orb_high side.
        For executed trades, an SL hit always means the opposite boundary was crossed.
        """
        violations = sum(1 for t in executed if t.exit_reason == ExitReason.SL_HIT)
        rate = round(violations / len(executed), 4) if executed else 0.0
        return violations, rate

    # ── High-risk symbols ──────────────────────────────────────────────────────

    @staticmethod
    def _identify_high_risk_symbols(executed: list) -> list[HighRiskSymbol]:
        """Return symbols where SL hit rate exceeds the high-risk threshold."""
        by_symbol: dict[str, list] = defaultdict(list)
        for trade in executed:
            by_symbol[trade.symbol].append(trade)

        high_risk: list[HighRiskSymbol] = []
        for symbol, trades in by_symbol.items():
            if len(trades) < _MIN_TRADES_FOR_ANALYSIS:
                continue
            sl_hit_list  = [t for t in trades if t.exit_reason == ExitReason.SL_HIT]
            sl_hit_rate  = len(sl_hit_list) / len(trades)
            if sl_hit_rate < _HIGH_RISK_SL_THRESHOLD:
                continue
            avg_loss = (
                round(statistics.mean(t.pnl for t in sl_hit_list), 2)
                if sl_hit_list else 0.0
            )
            high_risk.append(HighRiskSymbol(
                symbol=symbol,
                total_trades=len(trades),
                sl_hits=len(sl_hit_list),
                sl_hit_rate=round(sl_hit_rate, 4),
                avg_loss_when_sl=avg_loss,
            ))

        return sorted(high_risk, key=lambda s: s.sl_hit_rate, reverse=True)

    # ── Choppy-day behavior ────────────────────────────────────────────────────

    @staticmethod
    def _analyse_choppy_days(trades: list) -> tuple[list[str], float]:
        """
        Identify days where a high proportion of OSD candidates never broke out.

        A "choppy" day has >60% NO_BREAKOUT rate among candidates.
        Returns (list_of_choppy_date_strings, avg_no_breakout_rate_on_choppy_days).
        """
        by_date: dict[str, list] = defaultdict(list)
        for trade in trades:
            ref_time = trade.entry_time or trade.exit_time
            if ref_time is None:
                if hasattr(trade, "trading_date") and trade.trading_date:
                    date_key = trade.trading_date.astimezone(IST).date().isoformat()
                else:
                    continue
            else:
                date_key = ref_time.astimezone(IST).date().isoformat()
            by_date[date_key].append(trade)

        choppy_dates: list[str] = []
        no_breakout_rates: list[float] = []

        for date_str, day_trades in sorted(by_date.items()):
            if len(day_trades) < 3:
                continue
            nb = sum(1 for t in day_trades if t.exit_reason == ExitReason.NO_BREAKOUT)
            nb_rate = nb / len(day_trades)
            if nb_rate > 0.60:
                choppy_dates.append(date_str)
                no_breakout_rates.append(nb_rate)

        avg_nb_rate = round(statistics.mean(no_breakout_rates), 4) if no_breakout_rates else 0.0
        return choppy_dates, avg_nb_rate

    # ── SL clustering ─────────────────────────────────────────────────────────

    @staticmethod
    def _find_sl_cluster_days(sl_hits: list) -> tuple[list[str], int]:
        """
        Find days where 3 or more SL hits occurred — systematic risk days.

        Returns (list_of_cluster_date_strings, max_sl_hits_on_single_day).
        """
        by_date: dict[str, int] = defaultdict(int)
        for trade in sl_hits:
            if trade.exit_time:
                date_key = trade.exit_time.astimezone(IST).date().isoformat()
                by_date[date_key] += 1

        cluster_dates = sorted(d for d, count in by_date.items() if count >= 3)
        max_hits = max(by_date.values(), default=0)
        return cluster_dates, max_hits
