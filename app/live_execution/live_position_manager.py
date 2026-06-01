"""
Live position manager — in-memory book + exit detection for REAL positions.

Mirrors `PaperPositionManager` so the live and paper engines stay
structurally identical. The differences are:

  - Marks the candle close into a `LivePosition` document.
  - Exit decisions reference `LiveExitReason`.
  - Reconciliation: a periodic broker-positions read can reconcile the
    in-memory book against the broker's authoritative state (a closed
    position the engine did not see, an extra position the broker filled
    on its own, etc.).

The manager itself NEVER persists or broadcasts. It returns
`LiveExitDecision` objects to the service, which is responsible for the
exit-order placement, DB write, broadcast.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, time
from typing import Optional

from app.live.candle_builder import BuiltCandle
from app.models.live_position import (
    LiveExitReason,
    LivePosition,
    LivePositionStatus,
)
from app.models.live_order import LiveTradeSide
from app.utils.logger import get_logger
from app.utils.market_time import to_ist

logger = get_logger(__name__)


# ── Public dataclasses ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class LiveExitDecision:
    """Position-manager decision to close a live position."""

    position: LivePosition
    exit_reason: LiveExitReason
    exit_reference_price: float    # price at which the decision was taken
    detected_at: datetime          # candle end time / wall-clock


@dataclass(frozen=True)
class LivePriceUpdate:
    """A non-exit MTM tick from the live position manager."""

    position: LivePosition
    previous_price: float
    new_price: float


@dataclass(frozen=True)
class ReconciliationDiff:
    """A discrepancy between the in-memory book and the broker's positions."""

    symbol: str
    kind: str        # 'broker_only' | 'engine_only' | 'qty_mismatch'
    detail: dict


# ── Manager ──────────────────────────────────────────────────────────────────

def _unrealized_pnl(
    *, side: LiveTradeSide, quantity: int, entry_price: float, current_price: float
) -> float:
    """Side-aware mark-to-market P&L (₹)."""
    delta = current_price - entry_price
    if side is LiveTradeSide.SHORT:
        delta = -delta
    return round(delta * quantity, 4)


class LivePositionManager:
    """
    In-memory book of OPEN live positions.

    The service hydrates the book from MongoDB on startup, adds new
    positions on fill, and removes them on close. The manager owns the
    per-position `current_price` / `unrealized_pnl` snapshot.
    """

    def __init__(self, eod_exit_time_ist: time) -> None:
        self._eod_time_ist: time = eod_exit_time_ist
        # position_id -> LivePosition (OPEN only)
        self._book: dict[str, LivePosition] = {}
        self._by_symbol: dict[str, set[str]] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    # ── Book lifecycle ────────────────────────────────────────────────────────

    async def add_position(self, position: LivePosition) -> None:
        async with self._lock:
            self._book[position.position_id] = position
            self._by_symbol.setdefault(position.symbol.upper(), set()).add(
                position.position_id
            )
            logger.info(
                "[live-pm] position added: %s %s qty=%d entry=%.2f SL=%.2f",
                position.symbol, position.trade_side.value,
                position.quantity, position.average_price, position.stop_loss,
            )

    async def remove_position(self, position_id: str) -> Optional[LivePosition]:
        async with self._lock:
            removed = self._book.pop(position_id, None)
            if removed is not None:
                self._by_symbol.get(removed.symbol.upper(), set()).discard(position_id)
            return removed

    async def hydrate(self, positions: list[LivePosition]) -> None:
        """Replace the in-memory book with the given positions."""
        async with self._lock:
            self._book.clear()
            self._by_symbol.clear()
            for p in positions:
                self._book[p.position_id] = p
                self._by_symbol.setdefault(p.symbol.upper(), set()).add(p.position_id)
        logger.info("[live-pm] hydrated with %d open positions.", len(positions))

    # ── Read-only views ───────────────────────────────────────────────────────

    @property
    def open_count(self) -> int:
        return len(self._book)

    def get_open_positions(self) -> list[LivePosition]:
        return sorted(self._book.values(), key=lambda p: p.opened_at)

    def get_position(self, position_id: str) -> Optional[LivePosition]:
        return self._book.get(position_id)

    def has_open_for_symbol(self, symbol: str) -> bool:
        return bool(self._by_symbol.get(symbol.upper()))

    def total_exposure(self) -> float:
        """Sum of capital deployed across all open positions (₹)."""
        return round(
            sum(p.average_price * p.quantity for p in self._book.values()), 4
        )

    def aggregate_unrealized_pnl(self) -> float:
        return round(sum(p.unrealized_pnl for p in self._book.values()), 4)

    # ── Candle handler ───────────────────────────────────────────────────────

    async def on_candle(
        self, candle: BuiltCandle
    ) -> tuple[list[LivePriceUpdate], list[LiveExitDecision]]:
        """
        Apply a closed candle to every open live position for the candle's symbol.

        Returns (price_updates, exit_decisions).
        """
        symbol = candle.symbol.upper()
        ids = list(self._by_symbol.get(symbol, set()))
        if not ids:
            return [], []

        eod_reached = to_ist(candle.end_time).time() >= self._eod_time_ist

        updates: list[LivePriceUpdate] = []
        exits: list[LiveExitDecision] = []

        async with self._lock:
            for pid in ids:
                position = self._book.get(pid)
                if position is None:
                    continue

                previous_price = position.current_price
                position.current_price = candle.close
                position.unrealized_pnl = _unrealized_pnl(
                    side=position.trade_side,
                    quantity=position.quantity,
                    entry_price=position.average_price,
                    current_price=candle.close,
                )
                position.mark_updated()
                updates.append(
                    LivePriceUpdate(
                        position=position,
                        previous_price=previous_price,
                        new_price=candle.close,
                    )
                )

                # ── Exit detection (close-based, matches signal engine) ──
                if self._sl_hit(position, candle.close):
                    exits.append(
                        LiveExitDecision(
                            position=position,
                            exit_reason=LiveExitReason.SL_HIT,
                            exit_reference_price=position.stop_loss,
                            detected_at=candle.end_time,
                        )
                    )
                    continue

                if eod_reached:
                    exits.append(
                        LiveExitDecision(
                            position=position,
                            exit_reason=LiveExitReason.EOD_EXIT,
                            exit_reference_price=candle.close,
                            detected_at=candle.end_time,
                        )
                    )

        return updates, exits

    # ── Sweeps ────────────────────────────────────────────────────────────────

    async def collect_eod_exits(self, now_dt_utc: datetime) -> list[LiveExitDecision]:
        async with self._lock:
            return [
                LiveExitDecision(
                    position=position,
                    exit_reason=LiveExitReason.EOD_EXIT,
                    exit_reference_price=position.current_price,
                    detected_at=now_dt_utc,
                )
                for position in self._book.values()
            ]

    async def collect_halt_exits(self, now_dt_utc: datetime) -> list[LiveExitDecision]:
        async with self._lock:
            return [
                LiveExitDecision(
                    position=position,
                    exit_reason=LiveExitReason.RISK_HALT,
                    exit_reference_price=position.current_price,
                    detected_at=now_dt_utc,
                )
                for position in self._book.values()
            ]

    # ── Reconciliation ────────────────────────────────────────────────────────

    def reconcile_with_broker(
        self,
        broker_positions: dict[str, dict],
    ) -> list[ReconciliationDiff]:
        """
        Compare the in-memory book against the broker's positions.

        `broker_positions` is keyed by symbol with values
        `{"quantity": int, "average_price": float}`. The diff classifies
        every mismatch — the service decides how to react (sync the book,
        close orphans, alert the operator). The manager itself never
        mutates positions during reconciliation; that decision is
        deliberately left to a human + service layer to avoid silently
        squaring off real money.
        """
        diffs: list[ReconciliationDiff] = []
        engine_symbols: set[str] = set()

        for position in self._book.values():
            symbol = position.symbol.upper()
            engine_symbols.add(symbol)
            broker = broker_positions.get(symbol)
            engine_qty = (
                position.quantity
                if position.trade_side is LiveTradeSide.LONG
                else -position.quantity
            )

            if broker is None:
                diffs.append(
                    ReconciliationDiff(
                        symbol=symbol,
                        kind="engine_only",
                        detail={
                            "engine_qty": engine_qty,
                            "position_id": position.position_id,
                        },
                    )
                )
                continue

            broker_qty = int(broker.get("quantity", 0))
            if broker_qty != engine_qty:
                diffs.append(
                    ReconciliationDiff(
                        symbol=symbol,
                        kind="qty_mismatch",
                        detail={
                            "engine_qty": engine_qty,
                            "broker_qty": broker_qty,
                            "position_id": position.position_id,
                        },
                    )
                )

        for symbol, broker in broker_positions.items():
            if symbol.upper() not in engine_symbols:
                diffs.append(
                    ReconciliationDiff(
                        symbol=symbol.upper(),
                        kind="broker_only",
                        detail={"broker_qty": int(broker.get("quantity", 0))},
                    )
                )

        return diffs

    # ── Internal ──────────────────────────────────────────────────────────────

    @staticmethod
    def _sl_hit(position: LivePosition, close_price: float) -> bool:
        """Close-based stop-loss check (matches the live signal-engine convention)."""
        if position.trade_side is LiveTradeSide.LONG:
            return close_price <= position.stop_loss
        return close_price >= position.stop_loss
