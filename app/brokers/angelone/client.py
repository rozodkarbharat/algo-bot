"""
Angel One BaseBroker adapter — implements the broker-agnostic interface.

This is the public face of the Angel One integration to the live execution
engine. It composes:
  - `AngelOneAuth`           — session management
  - `AngelOneExecutionClient`— order-placement / book / positions HTTP layer

Translation map (broker-agnostic → Angel One):
  OrderSide.BUY/SELL   → transactiontype
  OrderType.MARKET     → "MARKET"
  OrderType.LIMIT      → "LIMIT"
  OrderType.SL         → "STOPLOSS_LIMIT"
  OrderType.SL_M       → "STOPLOSS_MARKET"
  ProductType.INTRADAY → "INTRADAY" (MIS)
  ProductType.DELIVERY → "DELIVERY" (CNC)
  ProductType.FUTURES  → "CARRYFORWARD" (NRML)

Symbol convention:
  - Angel One requires `tradingsymbol` with the `-EQ` suffix for equity
    cash market (e.g. "RELIANCE-EQ"). The adapter appends it when the
    caller passes a bare symbol and exchange == "NSE".
  - `symboltoken` is the instrument-master numeric id (looked up by the
    caller before invoking this adapter).

The adapter is responsible for **broker translation only** — risk
checks, idempotency, state machine and persistence all live in the
live-execution layer (`app/live_execution/`).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from app.brokers.angelone.auth import angel_one_auth
from app.brokers.angelone.execution import (
    AngelPlaceOrderRequest,
    AngelOneExecutionClient,
    angel_one_execution,
)
from app.brokers.base import (
    BaseBroker,
    MarginInfo,
    OrderResponse,
    OrderSide,
    OrderStatus,
    OrderType,
    PlaceOrderRequest,
    PositionInfo,
    ProductType,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

_BROKER_NAME = "AngelOne"


# ── Translation helpers ──────────────────────────────────────────────────────

_ORDER_TYPE_MAP: dict[OrderType, str] = {
    OrderType.MARKET: "MARKET",
    OrderType.LIMIT: "LIMIT",
    OrderType.SL: "STOPLOSS_LIMIT",
    OrderType.SL_M: "STOPLOSS_MARKET",
}

_PRODUCT_MAP: dict[ProductType, str] = {
    ProductType.INTRADAY: "INTRADAY",
    ProductType.DELIVERY: "DELIVERY",
    ProductType.FUTURES: "CARRYFORWARD",
}

# Angel One returns these status strings on getOrderBook/getOrderStatus.
# Map them to our canonical OrderStatus enum.
_STATUS_MAP: dict[str, OrderStatus] = {
    "open": OrderStatus.OPEN,
    "open pending": OrderStatus.OPEN,
    "trigger pending": OrderStatus.OPEN,
    "validation pending": OrderStatus.PENDING,
    "put order req received": OrderStatus.PENDING,
    "modify pending": OrderStatus.OPEN,
    "modified": OrderStatus.OPEN,
    "complete": OrderStatus.COMPLETE,
    "cancelled": OrderStatus.CANCELLED,
    "rejected": OrderStatus.REJECTED,
    "after market order req received": OrderStatus.PENDING,
}


def _trading_symbol(symbol: str, exchange: str) -> str:
    """Append the Angel One '-EQ' suffix for NSE/BSE cash equities."""
    if exchange.upper() in {"NSE", "BSE"} and not symbol.endswith("-EQ"):
        return f"{symbol}-EQ"
    return symbol


def _normalise_status(raw: Optional[str]) -> OrderStatus:
    if not raw:
        return OrderStatus.PENDING
    return _STATUS_MAP.get(raw.strip().lower(), OrderStatus.PENDING)


# ── Adapter ──────────────────────────────────────────────────────────────────

class AngelOneBroker(BaseBroker):
    """
    Angel One implementation of the BaseBroker interface.

    The live execution engine should depend on `BaseBroker`, not this
    concrete class — that keeps the engine swappable with a paper-broker
    or future Zerodha/Upstox adapter.
    """

    def __init__(
        self,
        execution_client: Optional[AngelOneExecutionClient] = None,
        auth=None,
    ) -> None:
        # Keep the auth and execution clients as instance attributes so
        # tests can inject fakes without monkey-patching modules.
        self._exec: AngelOneExecutionClient = execution_client or angel_one_execution
        self._auth = auth or angel_one_auth

    # ── Identity / session ────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return _BROKER_NAME

    async def login(self) -> None:
        # `get_session()` is idempotent: logs in if necessary, refreshes if
        # near expiry, returns the cached session otherwise.
        await self._auth.get_session()

    async def logout(self) -> None:
        await self._auth.logout()

    async def is_connected(self) -> bool:
        try:
            session = await self._auth.get_session()
            return session is not None and not session.is_expired()
        except Exception:
            return False

    # ── Order operations ──────────────────────────────────────────────────────

    async def place_order(self, request: PlaceOrderRequest) -> OrderResponse:
        """
        Translate the broker-agnostic request into Angel One's payload and
        submit it. The returned OrderResponse always carries broker_order_id.
        """
        # `tag` carries our `order_id` (UUID4 hex) for cross-system correlation.
        # Angel One uses it as the ordertag field — also persisted on the
        # LiveOrder row by the upstream layer.
        instrument_token = (request.tag or "").split("|")[1] if request.tag and "|" in request.tag else ""
        # Convention used by the live execution layer:
        #   tag = f"{order_id}|{instrument_token}"
        # This keeps the BaseBroker interface unchanged while smuggling the
        # mandatory symboltoken alongside the correlation id. The live
        # execution service is responsible for constructing the tag.
        if not instrument_token:
            raise ValueError(
                "PlaceOrderRequest.tag must encode 'order_id|instrument_token' "
                "for the Angel One adapter."
            )
        order_tag = request.tag.split("|", 1)[0] if request.tag else ""

        angel_req = AngelPlaceOrderRequest(
            variety=(
                "STOPLOSS"
                if request.order_type in (OrderType.SL, OrderType.SL_M)
                else "NORMAL"
            ),
            trading_symbol=_trading_symbol(request.symbol, request.exchange),
            symbol_token=instrument_token,
            transaction_type=request.side.value,  # "BUY" | "SELL"
            order_type=_ORDER_TYPE_MAP[request.order_type],
            product_type=_PRODUCT_MAP[request.product],
            duration="DAY",
            quantity=request.quantity,
            exchange=request.exchange,
            price=request.price,
            trigger_price=request.trigger_price,
            tag=order_tag or None,
        )
        resp = await self._exec.place_order(angel_req)
        return OrderResponse(
            broker_order_id=resp.broker_order_id,
            status=OrderStatus.OPEN,  # broker accepted; reconciliation refines later
            raw=resp.raw or {},
        )

    async def cancel_order(self, broker_order_id: str) -> bool:
        return await self._exec.cancel_order(broker_order_id)

    async def get_order_status(self, broker_order_id: str) -> OrderStatus:
        book = await self._exec.fetch_order_book()
        for entry in book:
            if str(entry.get("orderid")) == str(broker_order_id):
                return _normalise_status(entry.get("orderstatus") or entry.get("status"))
        return OrderStatus.PENDING

    # ── Portfolio reads ───────────────────────────────────────────────────────

    async def get_positions(self) -> list[PositionInfo]:
        rows = await self._exec.fetch_positions()
        positions: list[PositionInfo] = []
        for r in rows:
            try:
                qty = int(r.get("netqty", 0))
                if qty == 0:
                    continue
                positions.append(
                    PositionInfo(
                        symbol=r.get("tradingsymbol", ""),
                        exchange=r.get("exchange", "NSE"),
                        product=_product_from_angel(r.get("producttype", "")),
                        quantity=qty,
                        average_price=Decimal(str(r.get("avgnetprice", "0") or 0)),
                        last_price=Decimal(str(r.get("ltp", "0") or 0)),
                        pnl=Decimal(str(r.get("netvalue", "0") or 0)),
                    )
                )
            except Exception as exc:
                logger.warning("[AngelOne] skipping malformed position row: %s", exc)
        return positions

    async def get_margins(self) -> MarginInfo:
        # MarginInfo is currently informational only — the execution engine
        # uses LIVE_EXEC_TOTAL_CAPITAL for risk math because broker margin
        # reads are slow and sometimes 5xx. Returning zeros keeps the
        # contract satisfied without blocking execution on a margin call.
        return MarginInfo(
            available_cash=Decimal("0"),
            used_margin=Decimal("0"),
            total_margin=Decimal("0"),
        )

    async def get_ltp(self, symbol: str, exchange: str) -> Decimal:
        # The execution layer always knows the instrument_token alongside
        # the symbol. Callers who lack it should look it up via
        # `StockRepository.get_by_symbol(symbol).instrument_token` first.
        raise NotImplementedError(
            "get_ltp on the BaseBroker interface requires "
            "instrument_token; call AngelOneExecutionClient.fetch_ltp() "
            "directly with the token from app/repositories/stock_repository."
        )


def _product_from_angel(raw: str) -> ProductType:
    mapping = {
        "INTRADAY": ProductType.INTRADAY,
        "DELIVERY": ProductType.DELIVERY,
        "CARRYFORWARD": ProductType.FUTURES,
    }
    return mapping.get(raw.upper(), ProductType.INTRADAY)


# ── Module-level singleton ────────────────────────────────────────────────────

angel_one_broker = AngelOneBroker()
