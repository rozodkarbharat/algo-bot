"""
Angel One SmartAPI authentication module.

Handles:
  - Login with client credentials + TOTP
  - Session token caching (JWT, refresh token, feed token)
  - Proactive token refresh before expiry
  - Thread-safe singleton session via module-level instance

The session object is shared across auth.py and historical_data.py so only
one login round-trip occurs per application lifetime.

Angel One API docs:
  https://smartapi.angelbroking.com/docs
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
import pyotp

from app.brokers.angelone.rate_limiter import angel_one_rate_limiter
from app.config.settings import settings
from app.core.exceptions import AngelOneAuthException, AngelOneAPIException
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Angel One JWT tokens are valid for 24 hours; we refresh 30 min before expiry.
_TOKEN_EXPIRY_HOURS = 24
_REFRESH_BEFORE_MINUTES = 30


@dataclass
class AngelOneSession:
    """
    Holds active Angel One API credentials for a single session.

    jwt_token    — Bearer token for all API calls
    refresh_token — Used to get a new JWT without re-logging
    feed_token   — WebSocket market-feed authentication token
    expiry       — UTC datetime when jwt_token expires
    """

    jwt_token: str
    refresh_token: str
    feed_token: str
    expiry: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc) + timedelta(hours=_TOKEN_EXPIRY_HOURS)
    )

    def is_expired(self) -> bool:
        """True if the JWT has expired or will expire within the refresh window."""
        threshold = datetime.now(timezone.utc) + timedelta(minutes=_REFRESH_BEFORE_MINUTES)
        return self.expiry <= threshold

    def auth_headers(self, api_key: str) -> dict[str, str]:
        """Return the HTTP headers required by all Angel One API endpoints."""
        return {
            "Authorization": f"Bearer {self.jwt_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-UserType": "USER",
            "X-SourceID": "WEB",
            "X-ClientLocalIP": "CLIENT_LOCAL_IP",
            "X-ClientPublicIP": "CLIENT_PUBLIC_IP",
            "X-MACAddress": "MAC_ADDRESS",
            "X-PrivateKey": api_key,
        }


class AngelOneAuth:
    """
    Async Angel One authentication manager.

    Usage:
        auth = AngelOneAuth()
        session = await auth.get_session()   # logs in if no session exists
        headers = session.auth_headers(settings.ANGELONE_API_KEY)
    """

    LOGIN_PATH = "/rest/auth/angelbroking/user/v1/loginByPassword"
    REFRESH_PATH = "/rest/auth/angelbroking/jwt/v1/generateTokens"

    def __init__(self) -> None:
        self._session: Optional[AngelOneSession] = None
        self._lock = asyncio.Lock()

    # ── Public API ────────────────────────────────────────────────────────────

    async def get_session(self) -> AngelOneSession:
        """
        Return a valid session, logging in or refreshing as needed.
        Uses an asyncio.Lock to ensure only one login occurs concurrently.
        """
        async with self._lock:
            if self._session is None:
                await self._login()
            elif self._session.is_expired():
                await self._refresh()
        return self._session  # type: ignore[return-value]

    async def logout(self) -> None:
        """Invalidate the cached session (next call will re-login)."""
        async with self._lock:
            self._session = None
        logger.info("AngelOne session cleared.")

    async def invalidate_if_matches(self, session: "AngelOneSession") -> bool:
        """
        Atomically clear the cached session only if it is still the given `session`.

        Used when a downstream API call observes a 401/403 with a specific JWT:
        we want to evict that exact session so the next call re-logs in, but if
        another concurrent caller has already replaced the cache with a fresh
        session, we must leave that new session alone. Prevents the thundering
        herd of every in-flight 403 clobbering each other's re-login result.

        Returns True if the cache was cleared, False otherwise.
        """
        async with self._lock:
            if self._session is session:
                self._session = None
                logger.info("AngelOne session invalidated (stale JWT observed).")
                return True
            return False

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _login(self) -> None:
        """Perform a full login with credentials + TOTP."""
        self._validate_credentials()

        totp_secret = settings.ANGELONE_TOTP_SECRET
        if not totp_secret:
            raise AngelOneAuthException(
                "ANGELONE_TOTP_SECRET is not configured. "
                "Set it in .env to enable TOTP-based login."
            )

        totp = pyotp.TOTP(totp_secret).now()
        payload = {
            "clientcode": settings.ANGELONE_CLIENT_ID,
            "password": settings.ANGELONE_PASSWORD,
            "totp": totp,
        }

        logger.info("Logging in to Angel One as client %s…", settings.ANGELONE_CLIENT_ID)
        data = await self._post(self.LOGIN_PATH, payload, authenticated=False)

        self._session = AngelOneSession(
            jwt_token=data["jwtToken"],
            refresh_token=data["refreshToken"],
            feed_token=data["feedToken"],
        )
        logger.info("Angel One login successful.")

    async def _refresh(self) -> None:
        """Refresh the JWT token using the stored refresh token."""
        if self._session is None:
            await self._login()
            return

        logger.info("Refreshing Angel One JWT token…")
        try:
            payload = {
                "refreshToken": self._session.refresh_token,
            }
            data = await self._post(
                self.REFRESH_PATH, payload, authenticated=True, current_session=self._session
            )
            self._session = AngelOneSession(
                jwt_token=data["jwtToken"],
                refresh_token=data["refreshToken"],
                feed_token=data.get("feedToken", self._session.feed_token),
            )
            logger.info("Angel One token refreshed successfully.")
        except Exception as exc:
            logger.warning("Token refresh failed, falling back to full re-login: %s", exc)
            self._session = None
            await self._login()

    async def _post(
        self,
        path: str,
        payload: dict,
        authenticated: bool = True,
        current_session: Optional[AngelOneSession] = None,
    ) -> dict:
        """Execute a POST against the Angel One API and return the response data dict."""
        url = f"{settings.ANGELONE_BASE_URL}{path}"

        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-UserType": "USER",
            "X-SourceID": "WEB",
            "X-ClientLocalIP": "CLIENT_LOCAL_IP",
            "X-ClientPublicIP": "CLIENT_PUBLIC_IP",
            "X-MACAddress": "MAC_ADDRESS",
            "X-PrivateKey": settings.ANGELONE_API_KEY,
        }
        if authenticated and current_session:
            headers["Authorization"] = f"Bearer {current_session.jwt_token}"

        await angel_one_rate_limiter.acquire()

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
            except httpx.TimeoutException as exc:
                raise AngelOneAuthException(f"Request timed out: {exc}")
            except httpx.HTTPStatusError as exc:
                raise AngelOneAuthException(
                    f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
                )

        body: dict = response.json()
        if not body.get("status"):
            error_code = body.get("errorcode", "")
            message = body.get("message", "Unknown error")
            raise AngelOneAuthException(f"{message} (code={error_code})")

        return body.get("data") or body

    def _validate_credentials(self) -> None:
        """Raise early if required credentials are not configured."""
        missing = [
            name
            for name, val in [
                ("ANGELONE_API_KEY", settings.ANGELONE_API_KEY),
                ("ANGELONE_CLIENT_ID", settings.ANGELONE_CLIENT_ID),
                ("ANGELONE_PASSWORD", settings.ANGELONE_PASSWORD),
            ]
            if not val
        ]
        if missing:
            raise AngelOneAuthException(
                f"Missing Angel One credentials: {', '.join(missing)}. "
                "Set them in your .env file."
            )


# Module-level singleton — shared by all consumers (auth + historical data client).
angel_one_auth = AngelOneAuth()
