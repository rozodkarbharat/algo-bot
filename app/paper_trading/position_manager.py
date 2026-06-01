"""
Paper position manager — in-memory book + exit detection.

Responsibilities:
  - Hold the in-memory book of OPEN paper positions for low-latency
    access on every closed candle (LTP source).
  - Update `current_price` and `unrealized_pnl` from each candle close.
  - Detect stop-loss hits (close-based) and signal them upstream.
  - Detect EOD exits and signal them upstream.
  - Provide query helpers for the service layer.

The manager itself NEVER persists or broadcasts. It returns
`ExitDecision` objects to the service, which is responsible for the
DB write, account update, trade-ledger append, and WebSocket broadcast.

Concurrency:
  - One asyncio.Lock guards mutations of the position map. Callbacks
    are dispatched outside the lock to keep tick latency low.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Optional

from app.live.candle_builder import BuiltCandle
from app.models.paper_position import PaperPosition, PaperTradeSide
from app.models.paper_trade import PaperExitReason
from app.paper_trading.pnl_engine import calculate_unrealized_pnl
from app.utils.logger import get_logger
from app.utils.market_time import to_ist

logger = get_logger(__name__)


# ── Public dataclasses ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class ExitDecision:
    """
    Position-manager decision to close a position.

    The service uses this to actually exit the position (apply exit slippage,
    write the PaperTrade ledger row, update account totals, broadcast).
    """

    position: PaperPosition
    exit_reason: PaperExitReason
    exit_reference_price: float    # the price at which the manager decided to exit
    detected_at: datetime          # candle end time / wall clock


@dataclass(frozen=True)
class PriceUpdate:
    """A non-exit MTM tick from the position manager — info for the service."""

    position: PaperPosition
    previous_price: float
    new_price: float


# ── Manager ──────────────────────────────────────────────────────────────────

class PaperPositionManager:
    """
    In-memory book of OPEN paper positions.

    The service seeds the book on startup (replays open positions from
    Mongo) and on every new fill. The manager owns the per-position
    `current_price` / `unrealized_pnl` snapshot — repositories are
    written by the service.
    """

    def __init__(self, eod_exit_time_ist: time) -> None:
        self._eod_time_ist: time = eod_exit_time_ist
        # position_id -> PaperPosition (OPEN only)
        self._book: dict[str, PaperPosition] = {}
        # symbol -> set of position_ids (multi-day, multi-account ready)
        self._by_symbol: dict[str, set[str]] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    # ── Book lifecycle ────────────────────────────────────────────────────────

    async def add_position(self, position: PaperPosition) -> None:
        """Add a freshly opened position to the in-memory book."""
        async with self._lock:
            self._book[position.position_id] = position
            self._by_symbol.setdefault(position.symbol.upper(), set()).add(
                position.position_id
            )
            logger.info(
                "[paper-pm] position added: %s %s qty=%d entry=%.2f SL=%.2f",
                position.symbol, position.trade_side.value,
                position.quantity, position.entry_price, position.stop_loss,
            )

    async def remove_position(self, position_id: str) -> Optional[PaperPosition]:
        """Drop a position from the book (called after the service persists exit)."""
        async with self._lock:
            removed = self._book.pop(position_id, None)
            if removed is not None:
                self._by_symbol.get(removed.symbol.upper(), set()).discard(position_id)
            return removed

    async def hydrate(self, positions: list[PaperPosition]) -> None:
        """
        Replace the in-memory book with the given positions.

        Used on service start so a deploy mid-session can recover its
        OPEN positions from Mongo without losing track.
        """
        async with self._lock:
            self._book.clear()
            self._by_symbol.clear()
            for p in positions:
                self._book[p.position_id] = p
                self._by_symbol.setdefault(p.symbol.upper(), set()).add(p.position_id)
        logger.info("[paper-pm] hydrated with %d open positions.", len(positions))

    # ── Read-only views ───────────────────────────────────────────────────────

    @property
    def open_count(self) -> int:
        return len(self._book)

    def get_open_positions(self) -> list[PaperPosition]:
        """Return a snapshot list of OPEN positions (sorted by entry time)."""
        return sorted(self._book.values(), key=lambda p: p.opened_at)

    def get_position(self, position_id: str) -> Optional[PaperPosition]:
        return self._book.get(position_id)

    def get_open_for_symbol(self, symbol: str) -> list[PaperPosition]:
        ids = self._by_symbol.get(symbol.upper(), set())
        return [self._book[i] for i in ids if i in self._book]

    def has_open_for_symbol(self, symbol: str) -> bool:
        return bool(self._by_symbol.get(symbol.upper()))

    # ── Candle handler ───────────────────────────────────────────────────────

    async def on_candle(
        self, candle: BuiltCandle
    ) -> tuple[list[PriceUpdate], list[ExitDecision]]:
        """
        Apply a closed candle to every open position for the candle's symbol.

        Returns:
          (price_updates, exit_decisions)

        `price_updates` describes positions whose mark price was refreshed.
        `exit_decisions` describes positions that should be exited (SL hit
        or EOD reached). The service is responsible for actually exiting
        them — this keeps the manager pure and easily tested.
        """
        symbol = candle.symbol.upper()
        ids = list(self._by_symbol.get(symbol, set()))
        if not ids:
            return [], []

        eod_reached = to_ist(candle.end_time).time() >= self._eod_time_ist

        updates: list[PriceUpdate] = []
        exits: list[ExitDecision] = []

        async with self._lock:
            for pid in ids:
                position = self._book.get(pid)
                if position is None:
                    continue

                # Mark-to-market on the candle close. We deliberately use
                # close (not low/high) so SL detection is close-based —
                # matching the live signal engine's close-based breakout
                # convention and avoiding intra-bar look-ahead.
                previous_price = position.current_price
                position.current_price = candle.close
                position.unrealized_pnl = round(
                    calculate_unrealized_pnl(
                        trade_side=position.trade_side,
                        quantity=position.quantity,
                        entry_price=position.entry_price,
                        current_price=candle.close,
                    ),
                    4,
                )
                position.mark_updated()
                updates.append(
                    PriceUpdate(
                        position=position,
                        previous_price=previous_price,
                        new_price=candle.close,
                    )
                )

                # ── Exit detection ────────────────────────────────────────
                # Order matters: SL first, then EOD. SL on the same bar as
                # EOD still counts as SL_HIT (more conservative reporting).
                if self._sl_hit(position, candle.close):
                    exits.append(
                        ExitDecision(
                            position=position,
                            exit_reason=PaperExitReason.SL_HIT,
                            exit_reference_price=position.stop_loss,
                            detected_at=candle.end_time,
                        )
                    )
                    continue

                if eod_reached:
                    exits.append(
                        ExitDecision(
                            position=position,
                            exit_reason=PaperExitReason.EOD_EXIT,
                            exit_reference_price=candle.close,
                            detected_at=candle.end_time,
                        )
                    )

        return updates, exits

    # ── EOD sweep (called by session manager) ─────────────────────────────────

    async def collect_eod_exits(
        self, now_dt_utc: datetime
    ) -> list[ExitDecision]:
        """
        Return EOD exit decisions for every currently open position.

        Called by the session manager at EOD when no further candles are
        expected for some symbols. Exit price = current_price (last known LTP).
        """
        async with self._lock:
            exits = [
                ExitDecision(
                    position=position,
                    exit_reason=PaperExitReason.EOD_EXIT,
                    exit_reference_price=position.current_price,
                    detected_at=now_dt_utc,
                )
                for position in self._book.values()
            ]
            return exits

    async def collect_halt_exits(
        self, now_dt_utc: datetime
    ) -> list[ExitDecision]:
        """Force-close all open positions due to a risk halt."""
        async with self._lock:
            return [
                ExitDecision(
                    position=position,
                    exit_reason=PaperExitReason.RISK_HALT,
                    exit_reference_price=position.current_price,
                    detected_at=now_dt_utc,
                )
                for position in self._book.values()
            ]

    # ── Internal ──────────────────────────────────────────────────────────────

    @staticmethod
    def _sl_hit(position: PaperPosition, close_price: float) -> bool:
        """
        Close-based stop-loss check.

        LONG  : SL hit when close <= stop_loss
        SHORT : SL hit when close >= stop_loss
        """
        if position.trade_side is PaperTradeSide.LONG:
            return close_price <= position.stop_loss
        return close_price >= position.stop_loss
