"""
FastAPI application entry point.

Responsibilities:
  - Create and configure the FastAPI app instance
  - Register middleware (CORS, request logging)
  - Register exception handlers
  - Wire startup / shutdown lifecycle events
  - Mount routers (health, v1 API, WebSocket)

Run locally:
    uvicorn app.main:app --reload
"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.config.settings import settings
from app.core.exception_handlers import (
    trading_bot_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from app.core.exceptions import TradingBotException
from app.database.mongodb import connect_db, disconnect_db
from app.middleware.logging_middleware import RequestLoggingMiddleware
from app.middleware.rate_limit_middleware import RateLimitMiddleware
from app.middleware.security_headers_middleware import SecurityHeadersMiddleware
from app.routes import health
from app.routes.v1 import router as v1_router
from app.routes.websocket_routes import router as ws_router
from app.scheduler.scheduler import start_scheduler, stop_scheduler
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ── Lifespan (replaces deprecated on_event) ───────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Manages the application lifecycle.

    Startup order:
      1. Connect to MongoDB and initialise Beanie
      2. Start APScheduler

    Shutdown order (reverse):
      1. Stop APScheduler
      2. Disconnect MongoDB
    """
    logger.info("Starting %s [%s]…", settings.APP_NAME, settings.APP_ENV)

    # ── Startup ───────────────────────────────────────────────────────────────
    await connect_db()
    from app.services.auth_service import seed_admin_if_missing
    await seed_admin_if_missing()
    start_scheduler()

    logger.info("%s is ready.", settings.APP_NAME)
    yield  # Application is now running and serving requests

    # ── Shutdown ──────────────────────────────────────────────────────────────
    logger.info("Shutting down %s…", settings.APP_NAME)
    stop_scheduler()
    await disconnect_db()
    logger.info("%s shutdown complete.", settings.APP_NAME)


# ── App factory ───────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    """
    Application factory pattern.

    Creating the app via a factory makes it easy to instantiate multiple
    configurations in tests without module-level side effects.
    """
    app = FastAPI(
        title=settings.APP_NAME,
        description=(
            "Production-grade intraday algorithmic trading backend. "
            "Supports multiple brokers, strategies, and live WebSocket feeds."
        ),
        version="0.1.0",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # ── Middleware ────────────────────────────────────────────────────────────
    # Middleware is applied LIFO (last registered = outermost).
    # Outermost: RequestLogging → RateLimit → Security → CORS → routes

    # CORS — allow React dashboard and other approved origins.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Security headers on every response
    app.add_middleware(SecurityHeadersMiddleware)

    # Rate limiting — protects auth endpoints most aggressively
    app.add_middleware(RateLimitMiddleware)

    # Request logging must wrap all inner middleware so every request
    # (including OPTIONS preflight) is logged with its request_id.
    app.add_middleware(RequestLoggingMiddleware)

    # ── Exception handlers ────────────────────────────────────────────────────
    app.add_exception_handler(TradingBotException, trading_bot_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)

    # ── Routers ───────────────────────────────────────────────────────────────
    app.include_router(health.router)               # /health, /health/ready
    app.include_router(v1_router, prefix=settings.API_V1_PREFIX)  # /api/v1/…
    app.include_router(ws_router)                   # /ws/…

    return app


# Module-level app instance consumed by uvicorn / gunicorn.
app: FastAPI = create_app()
