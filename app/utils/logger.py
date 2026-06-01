"""
Centralised logging configuration.

Sets up:
  - Console handler (stdout, coloured in dev)
  - Rotating file handler → logs/app.log   (all levels ≥ INFO)
  - Rotating file handler → logs/error.log (ERROR and above only)

Import and use:
    from app.utils.logger import get_logger
    logger = get_logger(__name__)
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config.settings import settings

# ── Constants ─────────────────────────────────────────────────────────────────

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

MAX_BYTES = 10 * 1024 * 1024  # 10 MB per log file
BACKUP_COUNT = 5               # keep last 5 rotated files

_initialised = False


def _setup_logging() -> None:
    """
    Idempotent setup: configure the root logger once per process.

    Called automatically on first import so every module that calls
    get_logger() gets a properly configured logger without any extra setup.
    """
    global _initialised
    if _initialised:
        return

    log_dir = Path(settings.LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))

    formatter = logging.Formatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT)

    # ── Console handler ───────────────────────────────────────────────────────
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.DEBUG if settings.DEBUG else logging.INFO)
    root_logger.addHandler(console_handler)

    # ── app.log — all INFO+ messages ──────────────────────────────────────────
    app_file_handler = RotatingFileHandler(
        filename=log_dir / "app.log",
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    app_file_handler.setFormatter(formatter)
    app_file_handler.setLevel(logging.INFO)
    root_logger.addHandler(app_file_handler)

    # ── error.log — ERROR+ only ───────────────────────────────────────────────
    error_file_handler = RotatingFileHandler(
        filename=log_dir / "error.log",
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    error_file_handler.setFormatter(formatter)
    error_file_handler.setLevel(logging.ERROR)
    root_logger.addHandler(error_file_handler)

    # Suppress noisy third-party loggers
    logging.getLogger("motor").setLevel(logging.WARNING)
    logging.getLogger("pymongo").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    _initialised = True


def get_logger(name: str) -> logging.Logger:
    """
    Return a named logger, ensuring global logging is initialised first.

    Usage:
        logger = get_logger(__name__)
        logger.info("Server started")
    """
    _setup_logging()
    return logging.getLogger(name)
