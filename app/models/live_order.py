"""
Live order document — one row per real-money order sent to a broker.

A `LiveOrder` is the persistent ledger entry for every order the execution
engine places at a broker. It is the source of truth for the order's
lifecycle: from initial PENDING acknowledgement through OPEN, partial
fills, full fills, cancellation or rejection.

Persistence contract:
  - `order_id` is an application-generated UUID4 hex used as the
    client-side idempotency key (also passed to the broker as the
    tag/correlation id where the broker supports it).
  - `broker_order_id` is the broker-assigned id, populated after the
    broker accepts the order. Null while still PENDING.
  - `signal_id` ties the order back to the LiveSignal that produced it.
    A unique (signal_id, broker_name) index enforces "one order per
    signal per broker" — the engine's idempotency guarantee.
  - `trading_date` is stored as UTC midnight (matches LiveSignal /
    PaperPosition convention).

Multi-broker readiness:
  - `broker_name` is stamped on every row so reconciliation queries can
    filter by broker (e.g. only fetch AngelOne orders during the AngelOne
    poll job). New brokers add rows with their own `broker_name`.

The order state machine in `app/live_execution/order_state_machine.py`
is the only authority allowed to mutate `order_status`. Other writers
must go through the state machine to keep the audit trail consistent.
"""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Optional
from uuid import uuid4

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_order_id() -> str:
    """Compact, log-friendly order id (also used as broker correlation tag)."""
    return uuid4().hex


# ── Enums ─────────────────────────────────────────────────────────────────────

class LiveTradeSide(StrEnum):
    """Direction of the real trade."""

    LONG = "LONG"
    SHORT = "SHORT"


class LiveOrderType(StrEnum):
    """
    Broker-agnostic order type.

    Mirrors `app.brokers.base.OrderType` but lives in the model layer so
    storage does not depend on the broker package.
    """

    MARKET = "MARKET"
    LIMIT = "LIMIT"
    SL = "SL"            # stop-loss limit
    SL_M = "SL_M"        # stop-loss market


class LiveOrderStatus(StrEnum):
    """
    Order lifecycle states.

    Allowed transitions are enforced by the OrderStateMachine — see
    `app/live_execution/order_state_machine.py`.

      PENDING            — created locally; not yet acknowledged by broker
      OPEN               — broker accepted; resting on the order book
      PARTIALLY_FILLED   — some shares filled; remainder still open
      FILLED             — fully executed (terminal)
      CANCELLED          — cancelled by user/system before fully filling (terminal)
      REJECTED           — broker rejected at submission or modification (terminal)
    """

    PENDING = "PENDING"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


# ── Document ──────────────────────────────────────────────────────────────────

class LiveOrder(Document):
    """
    Persisted live order — append-and-update audit row.

    Collection: live_orders
    """

    # Application-generated correlation id (idempotency key for broker calls).
    order_id: str = Field(default_factory=_new_order_id)

    # Broker assigned id (None while PENDING). Indexed for reconciliation.
    broker_order_id: Optional[str] = Field(
        default=None, description="Broker-assigned order id (null until OPEN)"
    )

    # Provenance — link back to the LiveSignal that produced this order.
    signal_id: Optional[str] = Field(
        default=None, description="Source LiveSignal id (None for manual orders)"
    )

    # Identity of the broker that received this order. Allows multi-broker.
    broker_name: str = Field(..., description="e.g. 'AngelOne'")

    # ── Order shape ───────────────────────────────────────────────────────────
    symbol: str = Field(..., description="NSE ticker symbol")
    exchange: str = Field(default="NSE", description="Exchange code")

    order_type: LiveOrderType = Field(..., description="MARKET/LIMIT/SL/SL_M")
    trade_side: LiveTradeSide = Field(..., description="LONG or SHORT (BUY/SELL intent)")

    quantity: int = Field(..., description="Number of shares requested (always positive)")
    filled_quantity: int = Field(
        default=0, description="Cumulative shares filled (≤ quantity)"
    )

    requested_price: Optional[float] = Field(
        default=None,
        description="Limit/trigger reference price (None for MARKET orders)",
    )
    executed_price: Optional[float] = Field(
        default=None,
        description="VWAP of filled lots (None until first fill)",
    )

    # Stop-loss tied to this order at placement time. Persisted on the order
    # so the position manager can reconstruct SL state from the order ledger.
    stop_loss: Optional[float] = Field(
        default=None,
        description="Stop-loss level (price). Persisted on the order for audit.",
    )

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    order_status: LiveOrderStatus = Field(
        default=LiveOrderStatus.PENDING, description="State-machine controlled"
    )
    rejection_reason: Optional[str] = Field(
        default=None, description="Populated only on REJECTED transitions"
    )

    # ── Cost components (in ₹) ────────────────────────────────────────────────
    slippage: float = Field(
        default=0.0,
        description="Realised slippage (executed vs requested), absolute ₹",
    )
    brokerage: float = Field(
        default=0.0,
        description="Brokerage estimate at the time of placement (₹)",
    )

    # ── Trading session ───────────────────────────────────────────────────────
    trading_date: datetime = Field(
        ..., description="Trading date (UTC midnight) — matches LiveSignal"
    )

    # ── Audit ─────────────────────────────────────────────────────────────────
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    # State-transition log: append-only list of {from, to, at, reason}.
    # Kept on the order row (rather than a separate collection) so a single
    # read gives the full lifecycle — preferred for low-volume order data.
    transitions: list[dict] = Field(default_factory=list)

    metadata: dict = Field(default_factory=dict)

    class Settings:
        name = "live_orders"
        indexes = [
            IndexModel([("order_id", ASCENDING)], unique=True, name="order_id_unique"),
            # Broker order id is only unique when present; allow many nulls.
            IndexModel(
                [("broker_order_id", ASCENDING)],
                name="broker_order_id_idx",
                sparse=True,
            ),
            IndexModel([("symbol", ASCENDING)]),
            IndexModel([("order_status", ASCENDING)]),
            IndexModel([("signal_id", ASCENDING)], name="signal_id_idx", sparse=True),
            IndexModel([("trading_date", ASCENDING)]),
            IndexModel([("created_at", DESCENDING)]),
            # One order per signal per broker — the engine's primary
            # idempotency guarantee. Allows multi-broker execution because
            # the index is (signal_id, broker_name).
            IndexModel(
                [("signal_id", ASCENDING), ("broker_name", ASCENDING)],
                unique=True,
                sparse=True,
                name="signal_broker_unique",
            ),
            IndexModel(
                [("broker_name", ASCENDING), ("order_status", ASCENDING)],
                name="broker_status_idx",
            ),
        ]

    def mark_updated(self) -> None:
        self.updated_at = _utcnow()

    def is_terminal(self) -> bool:
        """True when the order has reached a non-mutable end state."""
        return self.order_status in {
            LiveOrderStatus.FILLED,
            LiveOrderStatus.CANCELLED,
            LiveOrderStatus.REJECTED,
        }
