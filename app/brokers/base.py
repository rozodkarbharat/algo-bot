"""
Abstract broker interface.

Every broker integration (AngelOne, Zerodha, Upstox, paper-trading, etc.)
must implement this interface. This ensures the strategy engine and order
service can call a unified API regardless of the actual broker in use.

The concrete implementations live in broker-specific submodules:
  app/brokers/angelone/client.py
  app/brokers/zerodha/client.py
  app/brokers/paper/client.py   ← simulated trading
"""

from abc import ABC, abstractmethod
from decimal import Decimal
from enum import StrEnum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Value objects ─────────────────────────────────────────────────────────────

class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    SL = "SL"          # stop-loss
    SL_M = "SL_M"      # stop-loss market


class ProductType(StrEnum):
    INTRADAY = "INTRADAY"   # MIS
    DELIVERY = "DELIVERY"   # CNC
    FUTURES = "FUTURES"     # NRML


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    COMPLETE = "COMPLETE"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class PlaceOrderRequest(BaseModel):
    """Broker-agnostic order placement payload."""

    symbol: str = Field(..., description="Trading symbol, e.g. 'RELIANCE'")
    exchange: str = Field(..., description="Exchange code, e.g. 'NSE'")
    side: OrderSide
    order_type: OrderType
    product: ProductType
    quantity: int = Field(..., gt=0)
    price: Optional[Decimal] = Field(None, description="Required for LIMIT orders")
    trigger_price: Optional[Decimal] = Field(None, description="Required for SL/SL_M orders")
    tag: Optional[str] = Field(None, description="Arbitrary label for strategy tracking")


class OrderResponse(BaseModel):
    """Broker-agnostic order acknowledgement."""

    broker_order_id: str
    status: OrderStatus
    raw: dict[str, Any] = Field(default_factory=dict, description="Raw broker response")


class PositionInfo(BaseModel):
    """Single open position as returned by the broker."""

    symbol: str
    exchange: str
    product: ProductType
    quantity: int          # positive = long, negative = short
    average_price: Decimal
    last_price: Decimal
    pnl: Decimal


class MarginInfo(BaseModel):
    available_cash: Decimal
    used_margin: Decimal
    total_margin: Decimal


# ── Abstract broker interface ─────────────────────────────────────────────────

class BaseBroker(ABC):
    """
    Contract that every broker adapter must fulfil.

    Concrete adapters translate these calls to the broker's native API.
    The order service depends only on this interface, keeping it
    broker-agnostic and testable with the paper-trading adapter.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable broker name, e.g. 'AngelOne'."""

    @abstractmethod
    async def login(self) -> None:
        """Authenticate with the broker. Stores session tokens internally."""

    @abstractmethod
    async def logout(self) -> None:
        """Invalidate the current session."""

    @abstractmethod
    async def is_connected(self) -> bool:
        """Return True if the session is currently valid."""

    @abstractmethod
    async def place_order(self, request: PlaceOrderRequest) -> OrderResponse:
        """Submit a new order and return the broker's acknowledgement."""

    @abstractmethod
    async def cancel_order(self, broker_order_id: str) -> bool:
        """Cancel an open order. Returns True on success."""

    @abstractmethod
    async def get_order_status(self, broker_order_id: str) -> OrderStatus:
        """Fetch the current status of a previously placed order."""

    @abstractmethod
    async def get_positions(self) -> list[PositionInfo]:
        """Return all open positions for the current session."""

    @abstractmethod
    async def get_margins(self) -> MarginInfo:
        """Return available and used margin information."""

    @abstractmethod
    async def get_ltp(self, symbol: str, exchange: str) -> Decimal:
        """Return the last traded price for a symbol."""
