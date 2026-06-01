"""
In-process sliding-window rate limiter.

Implemented as ASGI middleware (no external dependency like slowapi/Redis).
Uses an in-memory dict keyed by (IP, route_prefix) with a deque of request
timestamps within the current window.

Limits:
  - Auth endpoints (/api/v1/auth/):  RATE_LIMIT_AUTH_PER_MINUTE  (default 10/min)
  - All other endpoints:             RATE_LIMIT_PER_MINUTE        (default 120/min)

When AUTH_REQUIRED is False (dev mode), rate limiting is still applied but
uses a loopback IP so localhost dev traffic won't be throttled accidentally
unless DEBUG is also False.
"""

import time
from collections import defaultdict, deque
from typing import Callable, Deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config.settings import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

WINDOW_SECONDS = 60.0


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limiter."""

    def __init__(self, app: Callable) -> None:
        super().__init__(app)
        # Map (ip, bucket) → deque of request timestamps within the window
        self._windows: dict[tuple[str, str], Deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not settings.RATE_LIMIT_ENABLED:
            return await call_next(request)

        # Skip health probes — they come from internal monitors, not end users
        if request.url.path in ("/health", "/health/ready"):
            return await call_next(request)

        ip = self._get_ip(request)
        bucket, limit = self._classify(request)

        key = (ip, bucket)
        now = time.monotonic()
        window = self._windows[key]

        # Evict timestamps outside the current window
        cutoff = now - WINDOW_SECONDS
        while window and window[0] < cutoff:
            window.popleft()

        if len(window) >= limit:
            logger.warning("Rate limit exceeded: ip=%s bucket=%s", ip, bucket)
            return JSONResponse(
                status_code=429,
                content={
                    "error": "RateLimitExceeded",
                    "message": f"Too many requests. Limit: {limit} per minute.",
                    "retry_after": int(WINDOW_SECONDS - (now - window[0])),
                },
                headers={"Retry-After": str(int(WINDOW_SECONDS))},
            )

        window.append(now)
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(limit - len(window))
        return response

    def _get_ip(self, request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _classify(self, request: Request) -> tuple[str, int]:
        """Return (bucket_name, requests_per_window_limit)."""
        path = request.url.path
        if path.startswith("/api/v1/auth/"):
            return "auth", settings.RATE_LIMIT_AUTH_PER_MINUTE
        return "default", settings.RATE_LIMIT_PER_MINUTE
