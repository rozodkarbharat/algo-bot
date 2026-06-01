"""
Angel One SmartAPI execution client.

Low-level HTTP client for Angel One order-management endpoints. Mirrors
the style of `app/brokers/angelone/historical_data.py`:
  - Pure HTTP layer; no DB access, no WebSocket calls.
  - Uses the shared `angel_one_auth` singleton for JWT/session handling.
  - Translates non-200 responses and `status: false` payloads into typed
    application exceptions.
  - Retries transient failures with exponential back-off.
  - Honours a per-call lock to enforce a polite request rate.

API endpoints (all POST except orderBook/holdings which are GET):
  /rest/secure/angelbroking/order/v1/placeOrder
  /rest/secure/angelbroking/order/v1/modifyOrder
  /rest/secure/angelbroking/order/v1/cancelOrder
  /rest/secure/angelbroking/order/v1/getOrderBook   (GET)
  /rest/secure/angelbroking/order/v1/getPosition    (GET)
  /rest/secure/angelbroking/portfolio/v1/getAllHolding (GET)
  /rest/secure/angelbroking/order/v1/getLtpData     (POST)

The Angel One placeOrder payload (for an intraday equity market order):
    {
        "variety":     "NORMAL",         # NORMAL | STOPLOSS | AMO
        "tradingsymbol": "RELIANCE-EQ",
        "symboltoken":   "2885",
        "transactiontype": "BUY",        # BUY | SELL
        "ordertype":   "MARKET",         # MARKET | LIMIT | STOPLOSS_LIMIT | STOPLOSS_MARKET
        "producttype": "INTRADAY",       # INTRADAY | DELIVERY | CARRYFORWARD
        "duration":    "DAY",
        "price":       "0",
        "squareoff":   "0",
        "stoploss":    "0",
        "triggerprice":"0",
        "quantity":    "1",
        "exchange":    "NSE"
    }
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

import httpx

from app.brokers.angelone.auth import angel_one_auth
from app.config.settings import settings
from app.core.exceptions import (
    AngelOneAPIException,
    AngelOneAuthException,
    BrokerSessionExpiredException,
    OrderException,
    RateLimitException,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

# ── Endpoints ────────────────────────────────────────────────────────────────

PLACE_ORDER_PATH = "/rest/secure/angelbroking/order/v1/placeOrder"
MODIFY_ORDER_PATH = "/rest/secure/angelbroking/order/v1/modifyOrder"
CANCEL_ORDER_PATH = "/rest/secure/angelbroking/order/v1/cancelOrder"
ORDER_BOOK_PATH = "/rest/secure/angelbroking/order/v1/getOrderBook"
POSITIONS_PATH = "/rest/secure/angelbroking/order/v1/getPosition"
HOLDINGS_PATH = "/rest/secure/angelbroking/portfolio/v1/getAllHolding"
LTP_PATH = "/rest/secure/angelbroking/order/v1/getLtpData"

_BROKER_NAME = "AngelOne"


# ── Public dataclasses ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class AngelPlaceOrderRequest:
    """Internal, broker-shaped payload for placeOrder.

    The execution engine constructs broker-agnostic `PlaceOrderRequest`
    objects (from `app/brokers/base.py`); the Angel One adapter translates
    those into this struct before calling the HTTP client.
    """

    variety: str                  # NORMAL | STOPLOSS | AMO
    trading_symbol: str           # e.g. "RELIANCE-EQ"
    symbol_token: str
    transaction_type: str         # BUY | SELL
    order_type: str               # MARKET | LIMIT | STOPLOSS_LIMIT | STOPLOSS_MARKET
    product_type: str             # INTRADAY | DELIVERY | CARRYFORWARD
    duration: str                 # DAY | IOC
    quantity: int
    exchange: str                 # NSE | BSE | NFO | MCX
    price: Optional[Decimal] = None
    trigger_price: Optional[Decimal] = None
    square_off: Optional[Decimal] = None
    stop_loss: Optional[Decimal] = None
    tag: Optional[str] = None     # passes through as orderTag for correlation


@dataclass(frozen=True)
class AngelPlaceOrderResponse:
    """Acknowledgement returned by Angel One's placeOrder endpoint."""

    broker_order_id: str          # orderid
    unique_order_id: Optional[str] = None
    raw: dict[str, Any] = None  # type: ignore[assignment]


# ── Client ───────────────────────────────────────────────────────────────────

class AngelOneExecutionClient:
    """
    Async HTTP client for Angel One order-management.

    Concurrency:
      - A module-level lock serialises requests slightly to stay within
        Angel One's per-second rate limits without dropping below a 1Hz
        effective throughput. Heavier rate-limiting belongs in a dedicated
        token bucket if traffic grows.

    Retry policy:
      - Transient failures (timeouts, 5xx) retry up to
        LIVE_EXEC_ORDER_MAX_RETRIES times with exponential back-off.
      - 401 / token expiry surfaces as `BrokerSessionExpiredException` so
        the caller can trigger session refresh.
      - 429 surfaces as `RateLimitException`; the caller decides back-off.
    """

    def __init__(
        self,
        delay_seconds: Optional[float] = None,
        max_retries: Optional[int] = None,
        retry_backoff_seconds: Optional[float] = None,
    ) -> None:
        self._delay: float = (
            delay_seconds
            if delay_seconds is not None
            else settings.INGESTION_API_DELAY_SECONDS
        )
        self._max_retries: int = (
            max_retries
            if max_retries is not None
            else settings.LIVE_EXEC_ORDER_MAX_RETRIES
        )
        self._retry_backoff: float = (
            retry_backoff_seconds
            if retry_backoff_seconds is not None
            else settings.LIVE_EXEC_ORDER_RETRY_BACKOFF_SECONDS
        )
        self._lock = asyncio.Lock()

    # ── Public API ────────────────────────────────────────────────────────────

    async def place_order(
        self, request: AngelPlaceOrderRequest
    ) -> AngelPlaceOrderResponse:
        """
        Submit a new order. Returns the broker_order_id on success.

        Raises:
            OrderException — broker rejected the order (status:false)
            BrokerSessionExpiredException — 401 / token expiry
            RateLimitException — 429
            AngelOneAPIException — other broker / network errors
        """
        payload = self._build_place_payload(request)
        logger.info(
            "[AngelOne] placing order: %s %s qty=%d type=%s product=%s tag=%s",
            request.transaction_type, request.trading_symbol,
            request.quantity, request.order_type, request.product_type,
            request.tag,
        )
        data = await self._post(PLACE_ORDER_PATH, payload, op="placeOrder")
        broker_order_id = data.get("orderid")
        if not broker_order_id:
            raise OrderException(
                broker=_BROKER_NAME,
                message="placeOrder accepted but no orderid in response",
                detail=data,
            )
        return AngelPlaceOrderResponse(
            broker_order_id=str(broker_order_id),
            unique_order_id=data.get("uniqueorderid"),
            raw=data,
        )

    async def cancel_order(self, broker_order_id: str, variety: str = "NORMAL") -> bool:
        """Cancel an open order at the broker. Returns True on success."""
        payload = {"variety": variety, "orderid": broker_order_id}
        logger.info("[AngelOne] cancelling order %s (variety=%s)", broker_order_id, variety)
        try:
            await self._post(CANCEL_ORDER_PATH, payload, op="cancelOrder")
            return True
        except AngelOneAPIException as exc:
            # Broker may return "already cancelled" / "not found" as a non-success
            # status — surface it but don't crash the caller's loop.
            logger.warning("[AngelOne] cancel_order failed for %s: %s", broker_order_id, exc)
            return False

    async def modify_order(
        self,
        broker_order_id: str,
        new_quantity: Optional[int] = None,
        new_price: Optional[Decimal] = None,
        new_trigger_price: Optional[Decimal] = None,
        variety: str = "NORMAL",
    ) -> dict[str, Any]:
        """
        Modify an existing open order. Only the fields supplied are changed.

        Returns the raw broker response on success.
        """
        payload: dict[str, Any] = {
            "variety": variety,
            "orderid": broker_order_id,
        }
        if new_quantity is not None:
            payload["quantity"] = str(new_quantity)
        if new_price is not None:
            payload["price"] = str(new_price)
        if new_trigger_price is not None:
            payload["triggerprice"] = str(new_trigger_price)
        logger.info(
            "[AngelOne] modifying order %s qty=%s price=%s trigger=%s",
            broker_order_id, new_quantity, new_price, new_trigger_price,
        )
        return await self._post(MODIFY_ORDER_PATH, payload, op="modifyOrder")

    async def fetch_order_book(self) -> list[dict[str, Any]]:
        """Return the full order book for the session."""
        data = await self._get(ORDER_BOOK_PATH, op="getOrderBook")
        if isinstance(data, list):
            return data
        # Some Angel One responses wrap data in {"data": [...]}; _get returns
        # the inner data already, but defend against unexpected shapes.
        return []

    async def fetch_positions(self) -> list[dict[str, Any]]:
        """Return all open positions held in the broker account."""
        data = await self._get(POSITIONS_PATH, op="getPosition")
        if isinstance(data, list):
            return data
        return []

    async def fetch_holdings(self) -> list[dict[str, Any]]:
        """Return long-term holdings (CNC) from the broker account."""
        data = await self._get(HOLDINGS_PATH, op="getAllHolding")
        if isinstance(data, list):
            return data
        # Holdings endpoint can return {"holdings":[...], "totalholding": {...}}
        if isinstance(data, dict):
            return data.get("holdings", []) or []
        return []

    async def fetch_ltp(
        self, symbol: str, instrument_token: str, exchange: str
    ) -> Decimal:
        """Return the last traded price for a symbol."""
        payload = {
            "exchange": exchange,
            "tradingsymbol": symbol,
            "symboltoken": instrument_token,
        }
        data = await self._post(LTP_PATH, payload, op="getLtpData")
        ltp = data.get("ltp")
        if ltp is None:
            raise AngelOneAPIException(
                f"LTP unavailable for {symbol}", detail=data
            )
        return Decimal(str(ltp))

    # ── Internal: payload builders ────────────────────────────────────────────

    @staticmethod
    def _build_place_payload(req: AngelPlaceOrderRequest) -> dict[str, Any]:
        """Serialise an AngelPlaceOrderRequest into Angel One's payload shape."""
        payload: dict[str, Any] = {
            "variety": req.variety,
            "tradingsymbol": req.trading_symbol,
            "symboltoken": req.symbol_token,
            "transactiontype": req.transaction_type,
            "ordertype": req.order_type,
            "producttype": req.product_type,
            "duration": req.duration,
            "quantity": str(req.quantity),
            "exchange": req.exchange,
            # Defaults — overwritten below when supplied.
            "price": str(req.price) if req.price is not None else "0",
            "triggerprice": str(req.trigger_price) if req.trigger_price is not None else "0",
            "squareoff": str(req.square_off) if req.square_off is not None else "0",
            "stoploss": str(req.stop_loss) if req.stop_loss is not None else "0",
        }
        if req.tag:
            # Angel One accepts an `ordertag` field for correlation; falls
            # back gracefully if the broker ignores it.
            payload["ordertag"] = req.tag
        return payload

    # ── Internal: HTTP plumbing ───────────────────────────────────────────────

    async def _post(self, path: str, payload: dict, op: str) -> dict[str, Any]:
        """POST with auth, retries and unified error handling. Returns the `data` dict."""
        return await self._request("POST", path, payload=payload, op=op)

    async def _get(self, path: str, op: str) -> Any:
        """GET with auth, retries and unified error handling. Returns the `data` field."""
        return await self._request("GET", path, payload=None, op=op)

    async def _request(
        self,
        method: str,
        path: str,
        payload: Optional[dict],
        op: str,
    ) -> Any:
        """
        Execute a single HTTP request with retry / error translation.

        Order of error precedence:
          1. RateLimitException (429) — never retried; caller backs off.
          2. BrokerSessionExpiredException (401) — never retried at this level;
             caller refreshes session and re-tries.
          3. Transient HTTP / network errors — retried with back-off.
          4. status:false payloads — surfaced as OrderException / AngelOneAPIException.
        """
        url = f"{settings.ANGELONE_BASE_URL}{path}"
        last_exc: Optional[Exception] = None

        for attempt in range(1, self._max_retries + 2):  # initial + retries
            async with self._lock:
                try:
                    session = await angel_one_auth.get_session()
                except AngelOneAuthException as exc:
                    raise BrokerSessionExpiredException(_BROKER_NAME) from exc

                headers = session.auth_headers(settings.ANGELONE_API_KEY)
                try:
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        if method == "POST":
                            response = await client.post(url, json=payload, headers=headers)
                        else:
                            response = await client.get(url, headers=headers)
                except httpx.TimeoutException as exc:
                    last_exc = exc
                    logger.warning(
                        "[AngelOne] %s timed out (attempt %d/%d): %s",
                        op, attempt, self._max_retries + 1, exc,
                    )
                    await self._sleep_backoff(attempt)
                    continue
                except httpx.RequestError as exc:
                    last_exc = exc
                    logger.warning(
                        "[AngelOne] %s network error (attempt %d/%d): %s",
                        op, attempt, self._max_retries + 1, exc,
                    )
                    await self._sleep_backoff(attempt)
                    continue

                # ── Status code handling ──────────────────────────────────────
                if response.status_code == 429:
                    raise RateLimitException(source=f"AngelOne {op}", retry_after=60)
                if response.status_code == 401:
                    # Surface immediately so the auth layer can refresh.
                    raise BrokerSessionExpiredException(_BROKER_NAME)
                if 500 <= response.status_code < 600:
                    last_exc = AngelOneAPIException(
                        f"{op} HTTP {response.status_code}",
                        detail=response.text[:300],
                    )
                    logger.warning(
                        "[AngelOne] %s HTTP %d (attempt %d/%d)",
                        op, response.status_code, attempt, self._max_retries + 1,
                    )
                    await self._sleep_backoff(attempt)
                    continue
                if response.status_code >= 400:
                    raise AngelOneAPIException(
                        f"{op} HTTP {response.status_code}",
                        detail=response.text[:300],
                    )

                # ── Body parsing ──────────────────────────────────────────────
                try:
                    body: dict = response.json()
                except Exception as exc:
                    raise AngelOneAPIException(
                        f"{op} returned non-JSON response", detail=str(exc)
                    )

                if not body.get("status"):
                    error_code = body.get("errorcode") or body.get("errorCode") or ""
                    message = body.get("message", f"{op} failed")
                    # Order-related failures surface as OrderException for
                    # better caller-side classification.
                    if op in {"placeOrder", "modifyOrder", "cancelOrder"}:
                        raise OrderException(
                            broker=_BROKER_NAME,
                            message=f"{message} (errorcode={error_code})",
                            detail=body,
                        )
                    raise AngelOneAPIException(
                        message=message, error_code=str(error_code), detail=body
                    )

                # Light politeness throttle on success too.
                if self._delay > 0:
                    await asyncio.sleep(self._delay)

                return body.get("data") if "data" in body else body

        # All retries exhausted.
        raise AngelOneAPIException(
            f"{op} failed after {self._max_retries + 1} attempts",
            detail=str(last_exc) if last_exc else None,
        )

    async def _sleep_backoff(self, attempt: int) -> None:
        wait = self._retry_backoff * (2 ** (attempt - 1))
        await asyncio.sleep(wait)


# ── Module-level singleton ────────────────────────────────────────────────────

angel_one_execution = AngelOneExecutionClient()
