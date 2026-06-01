"""
FastAPI exception handlers.

Registered in app/main.py so every raised TradingBotException (and its
subclasses) is converted to a consistent JSON error response.

Response shape:
    {
        "error": "DocumentNotFoundException",
        "message": "Candle not found: 67a1bc...",
        "detail": null,
        "status_code": 404
    }
"""

import traceback
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.exceptions import TradingBotException
from app.utils.logger import get_logger

logger = get_logger(__name__)


async def trading_bot_exception_handler(
    request: Request, exc: TradingBotException
) -> JSONResponse:
    """Handle all application-specific exceptions."""
    if exc.status_code >= 500:
        logger.error(
            "Application error on %s %s: %s",
            request.method,
            request.url.path,
            exc,
            exc_info=True,
        )
    else:
        logger.warning(
            "Client error on %s %s: %s",
            request.method,
            request.url.path,
            exc,
        )

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.__class__.__name__,
            "message": exc.message,
            "detail": exc.detail,
            "status_code": exc.status_code,
        },
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Handle Pydantic request validation errors with a clean response."""
    logger.warning("Validation error on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=422,
        content={
            "error": "ValidationError",
            "message": "Request validation failed.",
            "detail": exc.errors(),
            "status_code": 422,
        },
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all for any exception not handled by a more specific handler."""
    logger.error(
        "Unhandled exception on %s %s:\n%s",
        request.method,
        request.url.path,
        traceback.format_exc(),
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": "InternalServerError",
            "message": "An unexpected internal error occurred.",
            "detail": None,
            "status_code": 500,
        },
    )
