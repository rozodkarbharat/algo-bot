"""
Signal validation document.

One document per per-signal comparison between expected and actual execution
results.  The compound unique index (signal_id, trading_mode) allows exactly
one PAPER record and one LIVE record per source signal.

Collection: signal_validations
"""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Optional
from uuid import uuid4

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, IndexModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ValidationTradingMode(StrEnum):
    PAPER = "PAPER"
    LIVE = "LIVE"


class SignalValidation(Document):
    """
    Persisted per-signal validation record.

    Captures the difference between what the strategy expected (from LiveSignal)
    and what actually happened (paper or live execution fill).

    Collection: signal_validations
    Unique constraints:
      - validation_id          — always unique (application id).
      - (signal_id, trading_mode) — one PAPER and one LIVE row per signal.
    """

    # ── Identity ──────────────────────────────────────────────────────────────
    validation_id: str = Field(default_factory=lambda: uuid4().hex)

    # Optional link to a ValidationRun; None for ad-hoc / standalone validations.
    run_id: Optional[str] = None

    # Mandatory link to LiveSignal.signal_id.
    signal_id: str

    symbol: str
    trading_date: datetime  # UTC midnight
    strategy_id: str = "one_side_orb"
    trading_mode: ValidationTradingMode  # PAPER or LIVE

    # ── Entry comparison ──────────────────────────────────────────────────────
    expected_entry: float  # LiveSignal.entry_price
    actual_entry: Optional[float] = None  # PaperTrade / LivePosition actual fill
    entry_difference: Optional[float] = None  # actual - expected (absolute ₹)
    entry_slippage_bps: Optional[float] = None  # (actual - expected) / expected * 10000

    # ── Exit comparison ───────────────────────────────────────────────────────
    expected_exit: Optional[float] = None  # stop_loss price (theoretical exit)
    actual_exit: Optional[float] = None
    exit_difference: Optional[float] = None
    exit_slippage_bps: Optional[float] = None

    # ── PnL comparison ────────────────────────────────────────────────────────
    expected_pnl: Optional[float] = None
    actual_pnl: Optional[float] = None
    pnl_difference: Optional[float] = None  # actual - expected

    # ── Latency fields (milliseconds) ─────────────────────────────────────────
    signal_latency_ms: Optional[float] = None    # signal.breakout_time -> signal.created_at
    execution_latency_ms: Optional[float] = None  # signal.created_at -> order/position created_at
    ws_latency_ms: Optional[float] = None         # WebSocket delivery latency

    # ── Execution status ──────────────────────────────────────────────────────
    is_executed: bool = False  # True if an actual fill was found
    is_missed: bool = False    # True if signal never executed (risk rejection or no fill)
    miss_reason: Optional[str] = None  # e.g. "risk_rejected", "no_breakout", "market_closed"

    created_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "signal_validations"
        indexes = [
            IndexModel(
                [("validation_id", ASCENDING)],
                unique=True,
                name="validation_id_unique",
            ),
            IndexModel([("signal_id", ASCENDING)], name="signal_validation_signal_id"),
            IndexModel([("symbol", ASCENDING)], name="signal_validation_symbol"),
            IndexModel([("trading_date", ASCENDING)], name="signal_validation_trading_date"),
            IndexModel([("strategy_id", ASCENDING)], name="signal_validation_strategy_id"),
            IndexModel([("trading_mode", ASCENDING)], name="signal_validation_trading_mode"),
            IndexModel([("is_missed", ASCENDING)], name="signal_validation_is_missed"),
            # One PAPER record and one LIVE record per source signal.
            IndexModel(
                [("signal_id", ASCENDING), ("trading_mode", ASCENDING)],
                unique=True,
                name="signal_id_trading_mode_unique",
            ),
        ]
