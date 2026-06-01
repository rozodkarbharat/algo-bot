"""
Latency tracker for the signal processing pipeline.

Measures three latency dimensions across the live trading pipeline:

1. Signal generation latency  — wall-clock time from the breakout candle
   timestamp (LiveSignal.breakout_time) to when the signal document was
   persisted (LiveSignal.created_at).  Clock-skew situations where
   breakout_time > created_at are clamped to 0 ms.

2. Execution latency — time from signal creation (LiveSignal.created_at)
   to when the first downstream position or order was created.  The lookup
   prefers PaperPosition (paper trading mode) and falls back to LiveOrder
   (live trading mode).

3. WebSocket broadcast latency — optional; sourced from SignalValidation
   records when available (ws_latency_ms field).

All percentiles (p50, p95, p99, max) are computed from in-memory sorted
lists using index arithmetic so no external statistics library is required.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from app.models.live_order import LiveOrder
from app.models.live_signal import LiveSignal
from app.models.paper_position import PaperPosition
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LatencyPercentiles:
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float


@dataclass(frozen=True)
class LatencyResult:
    # Signal generation latency: breakout_time -> signal.created_at
    avg_signal_latency_ms: float
    signal_latency_percentiles: LatencyPercentiles
    # Execution latency: signal.created_at -> order/position.created_at
    avg_execution_latency_ms: float
    execution_latency_percentiles: LatencyPercentiles
    # WebSocket latency: ws_latency_ms from SignalValidation records (if available)
    avg_ws_latency_ms: Optional[float]
    ws_latency_percentiles: Optional[LatencyPercentiles]
    sample_count: int
    high_latency_signals: list[dict]  # signals with latency > 2000ms: [{signal_id, latency_ms}]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _percentile(sorted_values: list[float], pct: float) -> float:
    """
    Return the value at the given percentile from a pre-sorted list.

    Uses index arithmetic:
        idx = int(len(sorted_values) * pct / 100)
    clamped to the last valid index so it never raises an IndexError.
    """
    idx = int(len(sorted_values) * pct / 100)
    return sorted_values[min(idx, len(sorted_values) - 1)]


def _build_percentiles(sorted_values: list[float]) -> LatencyPercentiles:
    """Compute p50/p95/p99/max from a non-empty sorted list."""
    return LatencyPercentiles(
        p50_ms=_percentile(sorted_values, 50),
        p95_ms=_percentile(sorted_values, 95),
        p99_ms=_percentile(sorted_values, 99),
        max_ms=sorted_values[-1],
    )


def _zero_percentiles() -> LatencyPercentiles:
    """Return all-zero percentiles when no data is available."""
    return LatencyPercentiles(p50_ms=0.0, p95_ms=0.0, p99_ms=0.0, max_ms=0.0)


# ---------------------------------------------------------------------------
# Tracker
# ---------------------------------------------------------------------------


class LatencyTracker:
    """
    Computes pipeline latency statistics for a given date range.

    Usage::

        tracker = LatencyTracker()
        result = await tracker.compute(from_date, to_date, strategy_id="my_strat")
    """

    async def compute(
        self,
        from_date: datetime,
        to_date: datetime,
        strategy_id: Optional[str] = None,
    ) -> LatencyResult:
        """
        Compute latency metrics for signals created within [from_date, to_date].

        Parameters
        ----------
        from_date:
            Lower bound (inclusive) on LiveSignal.created_at (UTC).
        to_date:
            Upper bound (inclusive) on LiveSignal.created_at (UTC).
        strategy_id:
            Optional filter; when provided only signals for this strategy are
            included.

        Returns
        -------
        LatencyResult
            Aggregated latency statistics and a list of high-latency signals.
        """
        # ------------------------------------------------------------------
        # 1. Fetch signals in the requested window
        # ------------------------------------------------------------------
        query_filter = {
            "created_at": {"$gte": from_date, "$lte": to_date},
        }
        if strategy_id is not None:
            query_filter["strategy_id"] = strategy_id

        signals: list[LiveSignal] = await LiveSignal.find(query_filter).to_list()

        logger.info(
            "LatencyTracker: fetched %d signals [%s – %s] strategy_id=%s",
            len(signals),
            from_date.isoformat(),
            to_date.isoformat(),
            strategy_id,
        )

        if not signals:
            logger.warning("LatencyTracker: no signals found — returning zero result")
            zero_p = _zero_percentiles()
            return LatencyResult(
                avg_signal_latency_ms=0.0,
                signal_latency_percentiles=zero_p,
                avg_execution_latency_ms=0.0,
                execution_latency_percentiles=zero_p,
                avg_ws_latency_ms=None,
                ws_latency_percentiles=None,
                sample_count=0,
                high_latency_signals=[],
            )

        # ------------------------------------------------------------------
        # 2. Compute signal generation latency per signal
        # ------------------------------------------------------------------
        signal_latencies: list[float] = []
        high_latency_signals: list[dict] = []

        for signal in signals:
            delta_ms = (
                (signal.created_at - signal.breakout_time).total_seconds() * 1000
            )
            # Clock skew guard
            if delta_ms < 0:
                logger.debug(
                    "LatencyTracker: clock skew on signal %s (delta=%.1f ms) — clamping to 0",
                    signal.signal_id,
                    delta_ms,
                )
                delta_ms = 0.0

            signal_latencies.append(delta_ms)

            if delta_ms > 2000:
                high_latency_signals.append(
                    {"signal_id": signal.signal_id, "latency_ms": delta_ms}
                )

        signal_latencies_sorted = sorted(signal_latencies)
        avg_signal_latency_ms = sum(signal_latencies) / len(signal_latencies)
        signal_percentiles = _build_percentiles(signal_latencies_sorted)

        logger.debug(
            "LatencyTracker: signal generation — avg=%.1f ms  p95=%.1f ms  high_latency=%d",
            avg_signal_latency_ms,
            signal_percentiles.p95_ms,
            len(high_latency_signals),
        )

        # ------------------------------------------------------------------
        # 3. Compute execution latency (signal.created_at -> position/order)
        # ------------------------------------------------------------------
        execution_latencies: list[float] = []

        # Build a lookup map: signal_id -> signal.created_at to avoid O(n²)
        signal_map: dict[str, datetime] = {s.signal_id: s.created_at for s in signals}
        signal_ids = list(signal_map.keys())

        # 3a. Fetch all matching PaperPositions in one query
        paper_positions: list[PaperPosition] = await PaperPosition.find(
            {"signal_id": {"$in": signal_ids}}
        ).to_list()

        covered_by_paper: set[str] = set()
        for pos in paper_positions:
            if pos.signal_id is None:
                continue
            signal_created_at = signal_map.get(pos.signal_id)
            if signal_created_at is None:
                continue
            # PaperPosition uses opened_at as the creation timestamp
            exec_ms = (
                (pos.opened_at - signal_created_at).total_seconds() * 1000
            )
            if exec_ms < 0:
                exec_ms = 0.0
            execution_latencies.append(exec_ms)
            covered_by_paper.add(pos.signal_id)

        # 3b. For signals not covered by a PaperPosition, try LiveOrder
        uncovered_ids = [sid for sid in signal_ids if sid not in covered_by_paper]

        if uncovered_ids:
            live_orders: list[LiveOrder] = await LiveOrder.find(
                {"signal_id": {"$in": uncovered_ids}}
            ).to_list()

            covered_by_order: set[str] = set()
            for order in live_orders:
                if order.signal_id is None:
                    continue
                if order.signal_id in covered_by_order:
                    # Only count the first order per signal
                    continue
                signal_created_at = signal_map.get(order.signal_id)
                if signal_created_at is None:
                    continue
                exec_ms = (
                    (order.created_at - signal_created_at).total_seconds() * 1000
                )
                if exec_ms < 0:
                    exec_ms = 0.0
                execution_latencies.append(exec_ms)
                covered_by_order.add(order.signal_id)

        if execution_latencies:
            execution_latencies_sorted = sorted(execution_latencies)
            avg_execution_latency_ms = sum(execution_latencies) / len(
                execution_latencies
            )
            execution_percentiles = _build_percentiles(execution_latencies_sorted)
        else:
            logger.warning(
                "LatencyTracker: no execution records found for %d signals",
                len(signals),
            )
            avg_execution_latency_ms = 0.0
            execution_percentiles = _zero_percentiles()

        logger.debug(
            "LatencyTracker: execution — avg=%.1f ms  p95=%.1f ms  samples=%d",
            avg_execution_latency_ms,
            execution_percentiles.p95_ms,
            len(execution_latencies),
        )

        # ------------------------------------------------------------------
        # 4. WebSocket latency — not persisted on LiveSignal or PaperPosition;
        #    this field lives on SignalValidation records which are not in the
        #    required import set.  The block below is a forward-compatible
        #    placeholder: it attempts an optional import and silently returns
        #    None when the model is unavailable or has no data.
        # ------------------------------------------------------------------
        avg_ws_latency_ms: Optional[float] = None
        ws_percentiles: Optional[LatencyPercentiles] = None

        try:
            from app.models.signal_validation import SignalValidation  # type: ignore[import]

            validations = await SignalValidation.find(
                {"signal_id": {"$in": signal_ids}}
            ).to_list()

            ws_latencies: list[float] = [
                float(v.ws_latency_ms)
                for v in validations
                if getattr(v, "ws_latency_ms", None) is not None
            ]

            if ws_latencies:
                ws_latencies_sorted = sorted(ws_latencies)
                avg_ws_latency_ms = sum(ws_latencies) / len(ws_latencies)
                ws_percentiles = _build_percentiles(ws_latencies_sorted)
                logger.debug(
                    "LatencyTracker: WebSocket — avg=%.1f ms  p95=%.1f ms  samples=%d",
                    avg_ws_latency_ms,
                    ws_percentiles.p95_ms,
                    len(ws_latencies),
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "LatencyTracker: WebSocket latency unavailable (%s)", exc
            )

        # ------------------------------------------------------------------
        # 5. Assemble and return result
        # ------------------------------------------------------------------
        return LatencyResult(
            avg_signal_latency_ms=avg_signal_latency_ms,
            signal_latency_percentiles=signal_percentiles,
            avg_execution_latency_ms=avg_execution_latency_ms,
            execution_latency_percentiles=execution_percentiles,
            avg_ws_latency_ms=avg_ws_latency_ms,
            ws_latency_percentiles=ws_percentiles,
            sample_count=len(signals),
            high_latency_signals=high_latency_signals,
        )
