"""
Paper execution engine — fill simulation for live signals.

Translates a `GeneratedSignal` from the Live Signal Engine into a simulated
fill: it computes a realistic entry price (with slippage), determines the
quantity from the configured capital-per-trade, charges entry-side brokerage,
and returns a `PaperFill` describing the position to be opened.

This module is intentionally pure:
  - No MongoDB I/O.
  - No broker imports (paper trading must remain swap-compatible with
    future real-broker execution at the SERVICE layer).
  - No WebSocket / scheduler dependencies.

It is also partial-fill ready — `PaperFill.filled_quantity` may be lower
than `PaperFill.requested_quantity` in the future without changing the
service signature.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.config.settings import settings
from app.live.signal_engine import GeneratedSignal
from app.models.live_signal import LiveSignalType
from app.models.paper_position import PaperTradeSide
from app.utils.logger import get_logger
from app.utils.market_time import now_utc

logger = get_logger(__name__)


# ── Public dataclasses ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class PaperFill:
    """
    Simulated fill result returned by the execution engine.

    The service is responsible for turning this into a PaperPosition
    document and persisting it. Keeping the fill separate from the
    Beanie document keeps execution pure and testable.
    """

    symbol: str
    trading_date: datetime         # UTC midnight
    trade_side: PaperTradeSide
    signal_id: Optional[str]
    requested_quantity: int
    filled_quantity: int
    entry_price: float             # incl. entry slippage
    raw_trigger_price: float       # what the signal observed
    stop_loss: float
    slippage_per_share: float      # signed; LONG positive, SHORT negative
    entry_brokerage: float
    capital_used: float
    fill_time: datetime
    strategy_id: str = "one_side_orb"
    strategy_name: str = "One-Side ORB"
    strategy_version: str = "1.0.0"
    metadata: dict = field(default_factory=dict)


# ── Engine ───────────────────────────────────────────────────────────────────

class PaperExecutionEngine:
    """
    Realistic fill simulator — slippage, brokerage, qty sizing.

    Sizing rule: shares = floor(capital_per_trade / entry_price); guarantees
    at least 1 share unless the trigger price exceeds the configured
    capital outright (in which case the service rejects the entry upstream).

    Slippage convention:
      - LONG  : entry price moves UP by slippage_pct (worse for the buyer).
      - SHORT : entry price moves DOWN by slippage_pct (worse for the seller).
    """

    def __init__(
        self,
        slippage_pct: Optional[float] = None,
        brokerage_per_side: Optional[float] = None,
        capital_per_trade: Optional[float] = None,
    ) -> None:
        self._slippage_pct: float = (
            slippage_pct if slippage_pct is not None else settings.PAPER_SLIPPAGE_PCT
        )
        self._brokerage: float = (
            brokerage_per_side
            if brokerage_per_side is not None
            else settings.PAPER_BROKERAGE_PER_SIDE
        )
        self._capital_per_trade: float = (
            capital_per_trade
            if capital_per_trade is not None
            else settings.PAPER_CAPITAL_PER_TRADE
        )

    # ── Pricing helpers ───────────────────────────────────────────────────────

    def apply_entry_slippage(
        self, trigger_price: float, side: PaperTradeSide
    ) -> float:
        """Return the slippage-adjusted entry price."""
        factor = (
            1.0 + self._slippage_pct / 100.0
            if side is PaperTradeSide.LONG
            else 1.0 - self._slippage_pct / 100.0
        )
        return round(trigger_price * factor, 4)

    def apply_exit_slippage(
        self, exit_price: float, side: PaperTradeSide
    ) -> float:
        """Return the slippage-adjusted exit price (always adverse)."""
        factor = (
            1.0 - self._slippage_pct / 100.0
            if side is PaperTradeSide.LONG
            else 1.0 + self._slippage_pct / 100.0
        )
        return round(exit_price * factor, 4)

    # ── Quantity sizing ───────────────────────────────────────────────────────

    def size_quantity(
        self, entry_price: float, available_capital: Optional[float] = None
    ) -> int:
        """
        Return the integer share count to deploy for one paper trade.

        - Uses the configured `capital_per_trade` ceiling.
        - If `available_capital` is supplied and smaller, the per-trade budget
          shrinks to fit. Always returns at least 1 if entry_price > 0; 0 if
          neither is feasible.
        """
        budget = self._capital_per_trade
        if available_capital is not None:
            budget = min(budget, available_capital)
        if entry_price <= 0 or budget <= 0:
            return 0
        qty = math.floor(budget / entry_price)
        return max(qty, 1) if budget >= entry_price else 0

    # ── Public API ────────────────────────────────────────────────────────────

    def simulate_fill(
        self,
        signal: GeneratedSignal,
        trading_dt_utc: datetime,
        available_capital: Optional[float] = None,
        fill_time: Optional[datetime] = None,
    ) -> PaperFill:
        """
        Build a `PaperFill` from a fresh `GeneratedSignal`.

        Args:
            signal: the breakout signal emitted by the live engine.
            trading_dt_utc: the UTC-midnight datetime for the trading date.
            available_capital: optional account-side capital ceiling. If
                provided and smaller than capital_per_trade, the deployed
                size is capped accordingly.
            fill_time: optional override for the simulated fill timestamp.
                Defaults to `now_utc()`.

        Returns a fully populated `PaperFill`. Quantity is guaranteed > 0
        because the upstream risk check rejects insufficient capital.
        """
        side = (
            PaperTradeSide.LONG
            if signal.signal_type is LiveSignalType.BUY
            else PaperTradeSide.SHORT
        )
        trigger_price = signal.entry_price
        entry_price = self.apply_entry_slippage(trigger_price, side)
        qty = self.size_quantity(entry_price, available_capital)
        capital_used = round(entry_price * qty, 4)
        slippage_per_share = round(entry_price - trigger_price, 4)

        fill = PaperFill(
            symbol=signal.symbol.upper(),
            trading_date=trading_dt_utc,
            trade_side=side,
            signal_id=None,  # service injects after persistence
            requested_quantity=qty,
            filled_quantity=qty,
            entry_price=entry_price,
            raw_trigger_price=trigger_price,
            stop_loss=signal.stop_loss,
            slippage_per_share=slippage_per_share,
            entry_brokerage=self._brokerage,
            capital_used=capital_used,
            fill_time=fill_time or now_utc(),
            strategy_id=signal.strategy_id,
            strategy_name=signal.strategy_name,
            strategy_version=signal.strategy_version,
            metadata={
                "signal_breakout_time": signal.breakout_time.isoformat(),
                "orb_high": signal.first_candle_high,
                "orb_low": signal.first_candle_low,
                "orb_range_percent": signal.orb_range_percent,
                "probability_score": signal.probability_score,
                "slippage_pct": self._slippage_pct,
                "capital_per_trade": self._capital_per_trade,
            },
        )
        logger.info(
            "[paper-exec] simulated fill: %s %s qty=%d entry=%.2f (trigger=%.2f) SL=%.2f",
            fill.symbol, side.value, qty, entry_price, trigger_price, signal.stop_loss,
        )
        return fill

    # ── Introspection ─────────────────────────────────────────────────────────

    @property
    def slippage_pct(self) -> float:
        return self._slippage_pct

    @property
    def brokerage_per_side(self) -> float:
        return self._brokerage

    @property
    def capital_per_trade(self) -> float:
        return self._capital_per_trade
