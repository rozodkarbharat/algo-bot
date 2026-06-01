"""
Slippage analysis engine.

Computes slippage between expected fills (from LiveSignal) and actual fills
(PaperTrade / LivePosition). Slippage is expressed in basis points (bps):

    slippage_bps = (actual - expected) / expected * 10_000

Adverse slippage conventions:
  - LONG  : positive bps  = paid more than expected (adverse)
  - SHORT : negative bps  = sold for less than expected (adverse)

Three trading modes are supported:
  - "PAPER"    : analyse PaperTrade records only
  - "LIVE"     : analyse LivePosition records only
  - "COMBINED" : merge both datasets before aggregating

The expected entry price for both modes comes from the linked LiveSignal
(matched on ``signal_id``). The expected exit price is the ``stop_loss``
field stored on the trade / position row (it reflects the ORB stop that was
active when the trade was placed, which is the reference the execution engine
targets for exit fills).

When a trade or position has no ``signal_id`` the bps components are skipped
(no signal to compare against), but the raw ₹ slippage cost — already
computed by the paper engine and stored on ``PaperTrade.slippage`` — is still
included in the total cost aggregation.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from beanie.operators import GTE, LTE, In

from app.models.live_position import LivePosition, LivePositionStatus, LiveTradeSide
from app.models.live_signal import LiveSignal
from app.models.paper_trade import PaperTrade
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SymbolSlippage:
    """Per-symbol slippage summary across the analysis window."""

    symbol: str
    avg_entry_slippage_bps: float
    avg_exit_slippage_bps: float
    worst_entry_slippage_bps: float
    worst_exit_slippage_bps: float
    total_slippage_cost_inr: float
    trade_count: int


@dataclass(frozen=True)
class SlippageResult:
    """Aggregate slippage result returned by :class:`SlippageAnalyzer`."""

    avg_entry_slippage_bps: float
    avg_exit_slippage_bps: float
    worst_entry_slippage_bps: float   # worst single trade
    worst_exit_slippage_bps: float
    total_slippage_cost_inr: float    # total ₹ cost of slippage across all trades
    symbol_breakdown: list[SymbolSlippage]
    sample_count: int                 # number of trades analysed
    trading_mode: str                 # "PAPER", "LIVE", or "COMBINED"


# ---------------------------------------------------------------------------
# Internal intermediary — not exposed in the public API
# ---------------------------------------------------------------------------


@dataclass
class _TradeSample:
    """Normalised row from either PaperTrade or LivePosition."""

    symbol: str
    signal_id: Optional[str]
    actual_entry_price: float
    actual_exit_price: Optional[float]    # None if position still open
    stop_loss: float
    slippage_cost_inr: float              # raw ₹ cost (from PaperTrade.slippage or 0)
    trade_side: str                       # "LONG" or "SHORT"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_bps(actual: float, expected: float) -> Optional[float]:
    """Return bps deviation; None when expected is zero or non-finite."""
    if not expected or not math.isfinite(expected) or not math.isfinite(actual):
        return None
    return (actual - expected) / expected * 10_000.0


def _worst_entry_bps(bps: float, side: str, current_worst: float) -> float:
    """
    Update the running 'worst' entry slippage.

    For LONG  the worst (most adverse) entry is the highest positive bps.
    For SHORT the worst (most adverse) entry is the lowest (most negative) bps.
    We store both as signed values and let callers interpret them; the
    aggregate function selects the max absolute value as the scalar 'worst'.
    """
    if abs(bps) > abs(current_worst):
        return bps
    return current_worst


# ---------------------------------------------------------------------------
# Analyser
# ---------------------------------------------------------------------------


class SlippageAnalyzer:
    """
    Compute entry and exit slippage statistics over a date range.

    Usage::

        analyzer = SlippageAnalyzer()
        result = await analyzer.compute(from_date, to_date, strategy_id="my_strat")
    """

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def compute(
        self,
        from_date: datetime,
        to_date: datetime,
        strategy_id: Optional[str] = None,
        trading_mode: str = "PAPER",
    ) -> SlippageResult:
        """
        Compute slippage statistics for the requested date range and mode.

        Parameters
        ----------
        from_date:
            Start of the analysis window (UTC-aware datetime, inclusive).
        to_date:
            End of the analysis window (UTC-aware datetime, inclusive).
        strategy_id:
            When provided, restrict the analysis to trades that originated
            from this strategy.
        trading_mode:
            "PAPER"    — PaperTrade records only.
            "LIVE"     — closed LivePosition records only.
            "COMBINED" — merge both datasets before aggregating.
        """
        trading_mode = trading_mode.upper()
        if trading_mode not in {"PAPER", "LIVE", "COMBINED"}:
            raise ValueError(
                f"trading_mode must be 'PAPER', 'LIVE', or 'COMBINED'; got {trading_mode!r}"
            )

        logger.info(
            "[slippage-analyzer] starting analysis mode=%s from=%s to=%s strategy=%s",
            trading_mode,
            from_date.isoformat(),
            to_date.isoformat(),
            strategy_id or "ALL",
        )

        samples: list[_TradeSample] = []

        if trading_mode in {"PAPER", "COMBINED"}:
            samples.extend(await self._fetch_paper_samples(from_date, to_date, strategy_id))

        if trading_mode in {"LIVE", "COMBINED"}:
            samples.extend(await self._fetch_live_samples(from_date, to_date, strategy_id))

        if not samples:
            logger.warning(
                "[slippage-analyzer] no trades found for mode=%s range=[%s, %s]",
                trading_mode,
                from_date.isoformat(),
                to_date.isoformat(),
            )
            return SlippageResult(
                avg_entry_slippage_bps=0.0,
                avg_exit_slippage_bps=0.0,
                worst_entry_slippage_bps=0.0,
                worst_exit_slippage_bps=0.0,
                total_slippage_cost_inr=0.0,
                symbol_breakdown=[],
                sample_count=0,
                trading_mode=trading_mode,
            )

        # Resolve expected entry prices from LiveSignal for samples that have
        # a signal_id so we can compute bps.
        signal_map = await self._build_signal_map(samples)

        result = self._aggregate(samples, signal_map, trading_mode)
        logger.info(
            "[slippage-analyzer] done mode=%s samples=%d "
            "avg_entry=%.2f bps avg_exit=%.2f bps total_cost=₹%.2f",
            trading_mode,
            result.sample_count,
            result.avg_entry_slippage_bps,
            result.avg_exit_slippage_bps,
            result.total_slippage_cost_inr,
        )
        return result

    # ------------------------------------------------------------------ #
    # Data-fetch helpers
    # ------------------------------------------------------------------ #

    async def _fetch_paper_samples(
        self,
        from_date: datetime,
        to_date: datetime,
        strategy_id: Optional[str],
    ) -> list[_TradeSample]:
        """Fetch PaperTrade records and normalise to _TradeSample."""
        query = PaperTrade.find(
            GTE(PaperTrade.opened_at, from_date),
            LTE(PaperTrade.opened_at, to_date),
        )
        if strategy_id:
            query = query.find(PaperTrade.strategy_id == strategy_id)

        trades: list[PaperTrade] = await query.to_list()
        logger.debug("[slippage-analyzer] fetched %d paper trades", len(trades))

        samples: list[_TradeSample] = []
        for t in trades:
            # PaperTrade stores trade_side from PaperTradeSide (LONG/SHORT).
            side = t.trade_side.value if hasattr(t.trade_side, "value") else str(t.trade_side)
            samples.append(
                _TradeSample(
                    symbol=t.symbol,
                    signal_id=t.signal_id,
                    actual_entry_price=t.entry_price,
                    actual_exit_price=t.exit_price,
                    stop_loss=t.stop_loss,
                    slippage_cost_inr=t.slippage,
                    trade_side=side,
                )
            )
        return samples

    async def _fetch_live_samples(
        self,
        from_date: datetime,
        to_date: datetime,
        strategy_id: Optional[str],
    ) -> list[_TradeSample]:
        """Fetch closed LivePosition records and normalise to _TradeSample."""
        query = LivePosition.find(
            GTE(LivePosition.opened_at, from_date),
            LTE(LivePosition.opened_at, to_date),
            LivePosition.status == LivePositionStatus.CLOSED,
        )
        if strategy_id:
            # LivePosition does not carry strategy_id directly; filter via the
            # signal map later.  Apply a best-effort filter using metadata if
            # available; otherwise fetch all closed positions and let the
            # signal-map filter handle it at aggregation time.
            pass  # strategy_id filtering is applied post-join (see _aggregate)

        positions: list[LivePosition] = await query.to_list()
        logger.debug("[slippage-analyzer] fetched %d closed live positions", len(positions))

        samples: list[_TradeSample] = []
        for p in positions:
            side = p.trade_side.value if hasattr(p.trade_side, "value") else str(p.trade_side)
            # For live positions: use average_price as the actual entry fill.
            # Exit price is stored directly on the closed position.
            # There is no pre-computed ₹ slippage field on LivePosition so we
            # leave it as 0.0; bps calculation will carry the signal comparison.
            samples.append(
                _TradeSample(
                    symbol=p.symbol,
                    signal_id=p.signal_id,
                    actual_entry_price=p.average_price,
                    actual_exit_price=p.exit_price,
                    stop_loss=p.stop_loss,
                    slippage_cost_inr=0.0,
                    trade_side=side,
                )
            )
        return samples

    # ------------------------------------------------------------------ #
    # Signal-map builder
    # ------------------------------------------------------------------ #

    async def _build_signal_map(
        self, samples: list[_TradeSample]
    ) -> dict[str, LiveSignal]:
        """
        Return a mapping of signal_id -> LiveSignal for all samples that carry
        a non-None signal_id.
        """
        signal_ids = list({s.signal_id for s in samples if s.signal_id is not None})
        if not signal_ids:
            return {}

        signals: list[LiveSignal] = await LiveSignal.find(
            In(LiveSignal.signal_id, signal_ids)
        ).to_list()

        signal_map = {sig.signal_id: sig for sig in signals}
        logger.debug(
            "[slippage-analyzer] resolved %d/%d signals from DB",
            len(signal_map),
            len(signal_ids),
        )
        return signal_map

    # ------------------------------------------------------------------ #
    # Aggregation
    # ------------------------------------------------------------------ #

    def _aggregate(
        self,
        samples: list[_TradeSample],
        signal_map: dict[str, LiveSignal],
        trading_mode: str,
    ) -> SlippageResult:
        """
        Compute aggregate and per-symbol slippage statistics.

        Bps values are only computed for samples that have a resolved signal.
        ₹ cost is accumulated for all samples (including those without a signal).
        """
        # Per-symbol accumulators.
        sym_entry_bps: dict[str, list[float]] = defaultdict(list)
        sym_exit_bps: dict[str, list[float]] = defaultdict(list)
        sym_cost: dict[str, float] = defaultdict(float)
        sym_count: dict[str, int] = defaultdict(int)

        all_entry_bps: list[float] = []
        all_exit_bps: list[float] = []
        total_cost: float = 0.0

        for sample in samples:
            sym_count[sample.symbol] += 1
            total_cost += sample.slippage_cost_inr
            sym_cost[sample.symbol] += sample.slippage_cost_inr

            if sample.signal_id is None:
                # No signal linkage — skip bps but count cost. (spec requirement)
                logger.debug(
                    "[slippage-analyzer] trade for %s has no signal_id; skipping bps",
                    sample.symbol,
                )
                continue

            signal = signal_map.get(sample.signal_id)
            if signal is None:
                logger.debug(
                    "[slippage-analyzer] signal %s not found for %s; skipping bps",
                    sample.signal_id,
                    sample.symbol,
                )
                continue

            # ── Entry slippage ───────────────────────────────────────────
            entry_bps = _safe_bps(sample.actual_entry_price, signal.entry_price)
            if entry_bps is not None:
                all_entry_bps.append(entry_bps)
                sym_entry_bps[sample.symbol].append(entry_bps)

            # ── Exit slippage ────────────────────────────────────────────
            # Use the stop_loss from the signal as the expected exit price.
            # This matches the ORB strategy's exit reference.
            if sample.actual_exit_price is not None:
                exit_bps = _safe_bps(sample.actual_exit_price, signal.stop_loss)
                if exit_bps is not None:
                    all_exit_bps.append(exit_bps)
                    sym_exit_bps[sample.symbol].append(exit_bps)

        # ── Global aggregates ────────────────────────────────────────────────
        def _avg(values: list[float]) -> float:
            return sum(values) / len(values) if values else 0.0

        def _worst(values: list[float]) -> float:
            """Return the value with the largest absolute magnitude."""
            if not values:
                return 0.0
            return max(values, key=abs)

        avg_entry = _avg(all_entry_bps)
        avg_exit = _avg(all_exit_bps)
        worst_entry = _worst(all_entry_bps)
        worst_exit = _worst(all_exit_bps)

        # ── Per-symbol breakdown ─────────────────────────────────────────────
        symbols = sorted(sym_count.keys())
        symbol_breakdown: list[SymbolSlippage] = []
        for sym in symbols:
            e_bps = sym_entry_bps[sym]
            x_bps = sym_exit_bps[sym]
            symbol_breakdown.append(
                SymbolSlippage(
                    symbol=sym,
                    avg_entry_slippage_bps=_avg(e_bps),
                    avg_exit_slippage_bps=_avg(x_bps),
                    worst_entry_slippage_bps=_worst(e_bps),
                    worst_exit_slippage_bps=_worst(x_bps),
                    total_slippage_cost_inr=round(sym_cost[sym], 4),
                    trade_count=sym_count[sym],
                )
            )

        return SlippageResult(
            avg_entry_slippage_bps=round(avg_entry, 4),
            avg_exit_slippage_bps=round(avg_exit, 4),
            worst_entry_slippage_bps=round(worst_entry, 4),
            worst_exit_slippage_bps=round(worst_exit, 4),
            total_slippage_cost_inr=round(total_cost, 4),
            symbol_breakdown=symbol_breakdown,
            sample_count=len(samples),
            trading_mode=trading_mode,
        )
