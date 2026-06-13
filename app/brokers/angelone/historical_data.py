"""
Angel One SmartAPI historical candle fetching client.

Wraps the Angel One getCandleData endpoint and returns normalised
CandleData objects that the ingestion service can persist directly.

API endpoint:
  POST /rest/secure/angelbroking/historical/v1/getCandleData

Request body:
  {
    "exchange":    "NSE",
    "symboltoken": "3045",
    "interval":    "FIFTEEN_MINUTE",
    "fromdate":    "2024-01-15 09:00",
    "todate":      "2024-01-15 15:30"
  }

Response data[]:
  Each element is [ISO_timestamp, open, high, low, close, volume]
  e.g. ["2024-01-15T09:15:00+05:30", 600.5, 605.0, 598.0, 602.0, 125000]
"""

import asyncio
from datetime import date, datetime
from typing import Optional

import httpx

from app.brokers.angelone.auth import angel_one_auth
from app.brokers.angelone.rate_limiter import angel_one_rate_limiter
from app.config.settings import settings
from app.core.exceptions import AngelOneAPIException, RateLimitException
from app.models.historical_candle import CandleData
from app.utils.candle_intervals import INTERVAL_MAX_DAYS, CandleInterval
from app.utils.logger import get_logger
from app.utils.market_time import (
    angel_one_date_str,
    market_close_datetime,
    market_open_datetime,
    parse_angel_one_timestamp,
)
from app.utils.trading_day import split_date_range

logger = get_logger(__name__)

HISTORICAL_DATA_PATH = "/rest/secure/angelbroking/historical/v1/getCandleData"

# Retry configuration for transient API errors.
_MAX_RETRIES = 3
_RETRY_DELAY_BASE = 2.0  # seconds, doubles each retry

# Substrings Angel One uses in a 403 body when the cause is rate-limiting
# rather than an invalid/stale JWT. Matched case-insensitively.
_RATE_LIMIT_BODY_MARKERS = ("exceeding access rate", "access rate", "rate limit")


def _is_rate_limit_body(body: Optional[str]) -> bool:
    """True if a 403 response body indicates rate-limiting (not a stale JWT)."""
    if not body:
        return False
    lowered = body.lower()
    return any(marker in lowered for marker in _RATE_LIMIT_BODY_MARKERS)


class AngelOneHistoricalClient:
    """
    Fetches historical OHLCV candles from Angel One SmartAPI.

    Automatically:
      - Splits large date ranges into API-compliant chunks
      - Retries on transient failures with exponential back-off
      - Enforces inter-request delays (rate-limit guard)
      - Parses raw API response into CandleData objects
    """

    # ── Public API ────────────────────────────────────────────────────────────

    async def fetch_historical_candles(
        self,
        symbol: str,
        instrument_token: str,
        exchange: str,
        interval: CandleInterval,
        from_date: date,
        to_date: date,
    ) -> list[CandleData]:
        """
        Fetch OHLCV candles for `symbol` between `from_date` and `to_date`.

        Large date ranges are automatically chunked to stay within Angel One's
        per-request limits. The results from all chunks are merged and returned
        as a flat, chronologically ordered list of CandleData objects.

        Args:
            symbol:           Ticker symbol (used only for logging).
            instrument_token: Angel One symboltoken for this instrument.
            exchange:         "NSE" | "BSE" | "NFO" | "MCX"
            interval:         CandleInterval enum value.
            from_date:        Start date (inclusive).
            to_date:          End date (inclusive).

        Returns:
            Flat sorted list of CandleData objects. Empty if no data available.
        """
        max_days = INTERVAL_MAX_DAYS[interval]
        chunks = split_date_range(from_date, to_date, chunk_days=max_days)
        all_candles: list[CandleData] = []

        for chunk_from, chunk_to in chunks:
            chunk_candles = await self._fetch_chunk(
                symbol=symbol,
                instrument_token=instrument_token,
                exchange=exchange,
                interval=interval,
                from_date=chunk_from,
                to_date=chunk_to,
            )
            all_candles.extend(chunk_candles)

        # De-duplicate and sort (Angel One occasionally returns overlapping candles at boundaries).
        seen: set[datetime] = set()
        unique: list[CandleData] = []
        for c in sorted(all_candles, key=lambda x: x.time):
            if c.time not in seen:
                seen.add(c.time)
                unique.append(c)

        logger.debug(
            "Fetched %d candles for %s [%s–%s] interval=%s",
            len(unique), symbol, from_date, to_date, interval,
        )
        return unique

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _fetch_chunk(
        self,
        symbol: str,
        instrument_token: str,
        exchange: str,
        interval: CandleInterval,
        from_date: date,
        to_date: date,
    ) -> list[CandleData]:
        """Fetch a single API-compliant date range chunk, with retries."""
        from_dt = market_open_datetime(from_date)
        to_dt = market_close_datetime(to_date)

        payload = {
            "exchange": exchange,
            "symboltoken": instrument_token,
            "interval": str(interval),
            "fromdate": angel_one_date_str(from_dt),
            "todate": angel_one_date_str(to_dt),
        }

        last_exc: Optional[Exception] = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                return await self._call_api(payload, symbol)
            except RateLimitException as exc:
                # The limiter was already paused inside _call_api, so the next
                # acquire() will block until the cooldown expires. Retry the
                # chunk instead of dropping the symbol entirely.
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    logger.warning(
                        "[%s] Rate-limited (attempt %d/%d); will retry after cooldown.",
                        symbol, attempt, _MAX_RETRIES,
                    )
                    continue
                logger.error(
                    "[%s] Rate-limited; retries exhausted for chunk %s–%s.",
                    symbol, from_date, to_date,
                )
            except AngelOneAPIException as exc:
                last_exc = exc
                detail = getattr(exc, "detail", None)
                detail_suffix = f" | response: {detail}" if detail else ""
                if attempt < _MAX_RETRIES:
                    wait = _RETRY_DELAY_BASE * (2 ** (attempt - 1))
                    logger.warning(
                        "[%s] API error (attempt %d/%d), retrying in %.1fs: %s%s",
                        symbol, attempt, _MAX_RETRIES, wait, exc, detail_suffix,
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error(
                        "[%s] All %d retries exhausted for chunk %s–%s: %s%s",
                        symbol, _MAX_RETRIES, from_date, to_date, exc, detail_suffix,
                    )
            except Exception as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(_RETRY_DELAY_BASE)
                else:
                    logger.error("[%s] Unexpected error after %d retries: %s", symbol, _MAX_RETRIES, exc)

        raise AngelOneAPIException(
            message=f"Failed to fetch chunk {from_date}–{to_date} for {symbol}",
            detail=str(last_exc),
        )

    async def _call_api(self, payload: dict, symbol: str) -> list[CandleData]:
        """Execute a single POST to the getCandleData endpoint."""
        await angel_one_rate_limiter.acquire()

        session = await angel_one_auth.get_session()
        url = f"{settings.ANGELONE_BASE_URL}{HISTORICAL_DATA_PATH}"
        headers = session.auth_headers(settings.ANGELONE_API_KEY)

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(url, json=payload, headers=headers)
            except httpx.TimeoutException as exc:
                raise AngelOneAPIException(f"Request timed out for {symbol}", detail=str(exc))
            except httpx.RequestError as exc:
                raise AngelOneAPIException(f"Network error for {symbol}", detail=str(exc))

        if response.status_code == 429:
            retry_after = 60
            await angel_one_rate_limiter.pause(retry_after)
            raise RateLimitException(source="AngelOne API", retry_after=retry_after)

        # Angel One signals rate-limiting on this endpoint with HTTP 403 +
        # body "Access denied because of exceeding access rate" (NOT 429).
        # Treat it as a rate limit: globally pause and back off. Do NOT evict
        # the session — it's valid, and re-logging in only adds more load.
        if response.status_code == 403 and _is_rate_limit_body(response.text):
            retry_after = 60
            logger.warning(
                "[%s] Angel One HTTP 403 rate-limit on getCandleData: %s",
                symbol, response.text[:200] or "<empty>",
            )
            await angel_one_rate_limiter.pause(retry_after)
            raise RateLimitException(source="AngelOne API", retry_after=retry_after)

        # Angel One returns 401/403 (not 401 only) for invalidated/stale JWTs
        # on the historical data endpoint — e.g. when another login on the same
        # client silently revoked our session. The retry path will only succeed
        # if we also evict the cached session so the next attempt re-logs in.
        if response.status_code in (401, 403):
            body_preview = response.text[:300] or "<empty>"
            logger.warning(
                "[%s] Angel One HTTP %d on getCandleData: %s",
                symbol, response.status_code, body_preview,
            )
            cleared = await angel_one_auth.invalidate_if_matches(session)
            if cleared:
                logger.info(
                    "[%s] Cleared cached Angel One session; next attempt will re-login.",
                    symbol,
                )
            raise AngelOneAPIException(
                f"HTTP {response.status_code} for {symbol}",
                detail=body_preview,
            )

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise AngelOneAPIException(
                f"HTTP {exc.response.status_code} for {symbol}",
                detail=exc.response.text[:300],
            )

        body: dict = response.json()
        if not body.get("status"):
            error_code = body.get("errorcode", "")
            message = body.get("message", "Unknown API error")
            raise AngelOneAPIException(message=message, error_code=error_code)

        raw_candles: list | None = body.get("data")
        if not raw_candles:
            # Empty data is valid (holiday, circuit, etc.) — return empty list.
            return []

        return self._parse_candles(raw_candles, symbol)

    @staticmethod
    def _parse_candles(raw: list, symbol: str) -> list[CandleData]:
        """
        Convert Angel One's raw candle arrays to CandleData objects.

        Each element format: [timestamp_str, open, high, low, close, volume]
        """
        candles: list[CandleData] = []
        for entry in raw:
            try:
                ts, o, h, l, c, v = entry
                candles.append(
                    CandleData(
                        time=parse_angel_one_timestamp(ts),
                        open=float(o),
                        high=float(h),
                        low=float(l),
                        close=float(c),
                        volume=int(v),
                    )
                )
            except (ValueError, TypeError) as exc:
                logger.warning("[%s] Skipping malformed candle entry %s: %s", symbol, entry, exc)
        return candles


# Module-level singleton.
angel_one_historical = AngelOneHistoricalClient()
