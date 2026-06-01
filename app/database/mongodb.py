"""
MongoDB connection manager using Motor (async driver) + Beanie ODM.

Lifecycle:
  connect_db()     — called in FastAPI startup event
  disconnect_db()  — called in FastAPI shutdown event

All Beanie document models are registered in init_db.py.
The Motor client is a module-level singleton; Motor's internal connection
pool handles concurrent requests without manual pooling logic here.
"""

import asyncio
from typing import Optional

import motor.motor_asyncio
from beanie import init_beanie
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

from app.config.settings import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Module-level client — shared across the entire application lifetime.
_client: Optional[AsyncIOMotorClient] = None  # type: ignore[type-arg]


def get_client() -> AsyncIOMotorClient:  # type: ignore[type-arg]
    """Return the active Motor client. Raises if not yet initialised."""
    if _client is None:
        raise RuntimeError(
            "MongoDB client is not initialised. "
            "Ensure connect_db() was called during application startup."
        )
    return _client


def get_database() -> AsyncIOMotorDatabase:  # type: ignore[type-arg]
    """Return the active Motor database handle."""
    return get_client()[settings.DATABASE_NAME]


async def connect_db(retries: int = 5, delay: float = 2.0) -> None:
    """
    Establish a Motor connection and initialise Beanie ODM.

    Retries up to `retries` times with exponential back-off so the
    container can start before MongoDB is ready (common in docker-compose).
    """
    global _client

    from app.database.init_db import get_document_models  # local import to avoid cycles

    for attempt in range(1, retries + 1):
        try:
            logger.info(
                "Connecting to MongoDB (attempt %d/%d): %s",
                attempt,
                retries,
                _redact_uri(settings.MONGO_URI),
            )
            _client = AsyncIOMotorClient(
                settings.MONGO_URI,
                maxPoolSize=settings.MONGO_MAX_CONNECTIONS,
                minPoolSize=settings.MONGO_MIN_CONNECTIONS,
                serverSelectionTimeoutMS=5000,
            )

            # Force a real handshake so we fail fast on bad credentials.
            await _client.admin.command("ping")

            db = _client[settings.DATABASE_NAME]
            await init_beanie(database=db, document_models=get_document_models())

            logger.info(
                "MongoDB connected. Database: '%s'", settings.DATABASE_NAME
            )
            return

        except (ConnectionFailure, ServerSelectionTimeoutError) as exc:
            logger.warning("MongoDB connection attempt %d failed: %s", attempt, exc)
            if attempt < retries:
                wait = delay * (2 ** (attempt - 1))  # exponential back-off
                logger.info("Retrying in %.1f seconds…", wait)
                await asyncio.sleep(wait)
            else:
                logger.error("All %d connection attempts failed. Aborting.", retries)
                raise


async def disconnect_db() -> None:
    """Close the Motor connection pool gracefully."""
    global _client
    if _client is not None:
        _client.close()
        _client = None
        logger.info("MongoDB connection closed.")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _redact_uri(uri: str) -> str:
    """Replace password in a MongoDB URI with '***' for safe logging."""
    try:
        from urllib.parse import urlparse, urlunparse
        parsed = urlparse(uri)
        if parsed.password:
            netloc = parsed.netloc.replace(parsed.password, "***")
            return urlunparse(parsed._replace(netloc=netloc))
    except Exception:
        pass
    return uri
