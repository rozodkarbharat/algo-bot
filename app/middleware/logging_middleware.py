"""
HTTP request/response logging middleware.

Logs every incoming request and its outcome with timing.
Attach to the FastAPI app in main.py:
    app.add_middleware(RequestLoggingMiddleware)
"""

import time
import uuid
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.utils.logger import get_logger

logger = get_logger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Assigns a unique request_id to each request and logs:
      - METHOD path, client IP
      - response status code and elapsed time in ms
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:  # type: ignore[override]
        request_id = str(uuid.uuid4())[:8]
        client = request.client.host if request.client else "unknown"

        start = time.perf_counter()
        logger.info(
            "[%s] → %s %s  (client: %s)",
            request_id,
            request.method,
            request.url.path,
            client,
        )

        try:
            response = await call_next(request)
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000
            logger.error(
                "[%s] ✗ %s %s  %.1f ms  UNHANDLED: %s",
                request_id,
                request.method,
                request.url.path,
                elapsed,
                exc,
            )
            raise

        elapsed = (time.perf_counter() - start) * 1000
        logger.info(
            "[%s] ← %s %s  %d  %.1f ms",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            elapsed,
        )

        # Propagate the request_id so callers can correlate logs.
        response.headers["X-Request-ID"] = request_id
        return response
