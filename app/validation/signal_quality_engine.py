"""
Signal Quality Engine — tracks signal conversion for the trading-bot platform.

Measures how many generated LiveSignals were actually executed (as PaperTrades
or LiveOrders) versus missed, and surfaces per-strategy breakdowns with miss
reason categorisation.

Typical usage::

    engine = SignalQualityEngine()
    result = await engine.compute(from_date, to_date, strategy_id="one_side_orb")
    print(result.conversion_rate, result.by_strategy)
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from app.models.live_order import LiveOrder, LiveOrderStatus
from app.models.live_signal import LiveSignal
from app.models.paper_position import PaperPosition
from app.models.paper_trade import PaperTrade
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ── Result types ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StrategySignalQuality:
    """Per-strategy signal conversion summary."""

    strategy_id: str
    generated: int
    executed: int
    missed: int
    conversion_rate: float  # executed / generated; 0.0 when generated == 0


@dataclass(frozen=True)
class SignalQualityResult:
    """Aggregate signal conversion result across all strategies in the window."""

    generated_count: int
    executed_count: int
    missed_count: int
    conversion_rate: float  # executed / generated; 0.0 when generated == 0
    miss_reasons: dict      # {"risk_rejected": N, "no_execution_attempted": N, "unknown": N}
    by_strategy: list[StrategySignalQuality]
    sample_days: int        # distinct trading_date values found in the signal set


# ── Engine ────────────────────────────────────────────────────────────────────


class SignalQualityEngine:
    """
    Computes signal-to-trade conversion metrics for a given date range.

    Algorithm
    ---------
    1. Fetch all LiveSignals in [from_date, to_date], optionally filtered by
       strategy_id.
    2. Build lookup sets of signal_ids that have a matching PaperTrade or a
       matching non-REJECTED LiveOrder.
    3. Classify each signal as executed or missed.
    4. For missed signals consult PaperPosition to determine miss_reason:
         - PaperPosition with status == "REJECTED" (or any rejected-like value)
           → "risk_rejected"
         - No PaperPosition at all → "no_execution_attempted"
         - PaperPosition exists but status is neither OPEN/CLOSED nor rejected
           → "unknown"
    5. Aggregate totals and per-strategy breakdowns, then return
       SignalQualityResult.
    """

    async def compute(
        self,
        from_date: datetime,
        to_date: datetime,
        strategy_id: Optional[str] = None,
    ) -> SignalQualityResult:
        """
        Compute signal quality metrics for the given window.

        Parameters
        ----------
        from_date:
            Inclusive start of the trading_date window (UTC).
        to_date:
            Inclusive end of the trading_date window (UTC).
        strategy_id:
            When provided, restrict analysis to signals from this strategy.
            When None, all strategies are included.

        Returns
        -------
        SignalQualityResult
        """
        logger.info(
            "SignalQualityEngine.compute: from=%s to=%s strategy_id=%s",
            from_date.isoformat(),
            to_date.isoformat(),
            strategy_id,
        )

        # ── 1. Fetch signals ──────────────────────────────────────────────────
        signal_query = LiveSignal.find(
            LiveSignal.trading_date >= from_date,
            LiveSignal.trading_date <= to_date,
        )
        if strategy_id is not None:
            signal_query = signal_query.find(LiveSignal.strategy_id == strategy_id)

        signals: list[LiveSignal] = await signal_query.to_list()

        if not signals:
            logger.info("SignalQualityEngine: no signals found for the given window")
            return SignalQualityResult(
                generated_count=0,
                executed_count=0,
                missed_count=0,
                conversion_rate=0.0,
                miss_reasons={},
                by_strategy=[],
                sample_days=0,
            )

        all_signal_ids: list[str] = [s.signal_id for s in signals]
        logger.debug("SignalQualityEngine: %d signals fetched", len(all_signal_ids))

        # ── 2a. Find executed via PaperTrade (any status — trades are completed)
        paper_trades = await PaperTrade.find(
            {"signal_id": {"$in": all_signal_ids}}
        ).to_list()
        executed_via_paper: set[str] = {
            pt.signal_id for pt in paper_trades if pt.signal_id is not None
        }

        # ── 2b. Find executed via LiveOrder — exclude REJECTED orders ─────────
        non_rejected_orders = await LiveOrder.find(
            LiveOrder.signal_id.in_(all_signal_ids),  # type: ignore[attr-defined]
            LiveOrder.order_status != LiveOrderStatus.REJECTED,
        ).to_list()
        executed_via_order: set[str] = {
            lo.signal_id for lo in non_rejected_orders if lo.signal_id is not None
        }

        executed_signal_ids: set[str] = executed_via_paper | executed_via_order
        missed_signal_ids: list[str] = [
            sid for sid in all_signal_ids if sid not in executed_signal_ids
        ]

        logger.debug(
            "SignalQualityEngine: executed=%d  missed=%d",
            len(executed_signal_ids),
            len(missed_signal_ids),
        )

        # ── 3. Miss reason detection via PaperPosition ───────────────────────
        miss_reasons: dict[str, int] = defaultdict(int)
        if missed_signal_ids:
            paper_positions = await PaperPosition.find(
                {"signal_id": {"$in": missed_signal_ids}}
            ).to_list()
            # Map signal_id → PaperPosition (take first if multiple)
            position_by_signal: dict[str, PaperPosition] = {}
            for pp in paper_positions:
                if pp.signal_id and pp.signal_id not in position_by_signal:
                    position_by_signal[pp.signal_id] = pp

            for sid in missed_signal_ids:
                if sid not in position_by_signal:
                    miss_reasons["no_execution_attempted"] += 1
                else:
                    pos = position_by_signal[sid]
                    status_val = (
                        pos.status.value
                        if hasattr(pos.status, "value")
                        else str(pos.status)
                    ).upper()
                    if "REJECT" in status_val:
                        miss_reasons["risk_rejected"] += 1
                    else:
                        miss_reasons["unknown"] += 1

        # ── 4. Sample days (distinct trading_date values) ─────────────────────
        sample_days: int = len({s.trading_date for s in signals})

        # ── 5. Per-strategy breakdown ─────────────────────────────────────────
        strategy_generated: dict[str, list[str]] = defaultdict(list)
        for sig in signals:
            strategy_generated[sig.strategy_id].append(sig.signal_id)

        by_strategy: list[StrategySignalQuality] = []
        for strat_id, sig_ids in strategy_generated.items():
            s_executed = sum(1 for sid in sig_ids if sid in executed_signal_ids)
            s_generated = len(sig_ids)
            s_missed = s_generated - s_executed
            s_rate = s_executed / s_generated if s_generated else 0.0
            by_strategy.append(
                StrategySignalQuality(
                    strategy_id=strat_id,
                    generated=s_generated,
                    executed=s_executed,
                    missed=s_missed,
                    conversion_rate=round(s_rate, 6),
                )
            )

        # Sort for deterministic output — highest conversion first
        by_strategy.sort(key=lambda x: x.conversion_rate, reverse=True)

        # ── 6. Totals ─────────────────────────────────────────────────────────
        generated_count = len(all_signal_ids)
        executed_count = len(executed_signal_ids)
        missed_count = len(missed_signal_ids)
        conversion_rate = executed_count / generated_count if generated_count else 0.0

        logger.info(
            "SignalQualityEngine: generated=%d executed=%d missed=%d "
            "conversion_rate=%.4f sample_days=%d",
            generated_count,
            executed_count,
            missed_count,
            conversion_rate,
            sample_days,
        )

        return SignalQualityResult(
            generated_count=generated_count,
            executed_count=executed_count,
            missed_count=missed_count,
            conversion_rate=round(conversion_rate, 6),
            miss_reasons=dict(miss_reasons),
            by_strategy=by_strategy,
            sample_days=sample_days,
        )
