"""
Telegram Bot notification provider.

Implements BaseNotifier using the Telegram Bot API (sendMessage endpoint).

Features:
  - MarkdownV2 formatted messages via telegram_templates.py
  - Async HTTP via httpx (consistent with the rest of the codebase)
  - Exponential back-off retry (max 3 attempts: 2s, 4s, 8s)
  - Per-message rate limiting: respects Telegram's 30 msg/s limit by
    tracking the last send timestamp and inserting a micro-sleep when
    under the threshold
  - Distinct send paths for each BaseNotifier method type
"""

import asyncio
from typing import Any, Optional

import httpx

from app.config.settings import settings
from app.notifications.base_notifier import AlertSeverity, BaseNotifier, NotificationEventType
from app.notifications import templates as _tpl_pkg  # noqa: F401 — resolved below
from app.notifications.templates import telegram_templates as tpl
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Telegram Bot API base URL.
_API_BASE = "https://api.telegram.org/bot"

# Minimum seconds between consecutive sends to stay under Telegram rate limits.
_MIN_SEND_INTERVAL = 0.05  # 20 msg/s — conservative headroom below the 30/s cap

# Retry configuration.
_MAX_RETRIES = 3
_BACKOFF_BASE = 2.0  # seconds; attempt n waits BASE * 2^(n-1)


class TelegramNotifier(BaseNotifier):
    """
    Notification provider for Telegram Bot API.

    Reads credentials from settings at dispatch time (not at construction)
    so the class can be instantiated before the .env file is loaded in tests.
    """

    def __init__(self) -> None:
        self._last_send_at: float = 0.0

    # ── BaseNotifier interface ────────────────────────────────────────────────

    @property
    def channel_name(self) -> str:
        return "telegram"

    @property
    def is_enabled(self) -> bool:
        return (
            settings.ALERT_TELEGRAM_ENABLED
            and bool(settings.ALERT_TELEGRAM_BOT_TOKEN)
            and bool(settings.ALERT_TELEGRAM_CHAT_ID)
        )

    async def send_message(
        self,
        title: str,
        body: str,
        severity: AlertSeverity = AlertSeverity.INFO,
        payload: Optional[dict[str, Any]] = None,
    ) -> bool:
        text = tpl.generic_message(title, body, severity)
        return await self._send(text)

    async def send_error(
        self,
        component: str,
        error: str,
        detail: str = "",
    ) -> bool:
        text = tpl.system_error(component, error, detail)
        return await self._send(text)

    async def send_trade_alert(
        self,
        event_type: NotificationEventType,
        symbol: str,
        side: str,
        entry_price: float,
        stop_loss: float,
        quantity: int,
        pnl: Optional[float] = None,
        extra: Optional[dict[str, Any]] = None,
    ) -> bool:
        extra = extra or {}
        mode = extra.get("mode", "Paper")

        if event_type in (
            NotificationEventType.PAPER_TRADE_ENTERED,
            NotificationEventType.LIVE_TRADE_ENTERED,
        ):
            capital_used = entry_price * quantity
            text = tpl.trade_entered(mode, symbol, side, entry_price, stop_loss, quantity, capital_used)

        elif event_type == NotificationEventType.STOP_LOSS_HIT:
            text = tpl.stop_loss_hit(mode, symbol, side, stop_loss, pnl or 0.0)

        elif event_type in (
            NotificationEventType.PAPER_TRADE_EXITED,
            NotificationEventType.LIVE_TRADE_EXITED,
        ):
            text = tpl.trade_exited(
                mode,
                symbol,
                side,
                entry_price,
                extra.get("exit_price", entry_price),
                quantity,
                pnl or 0.0,
                extra.get("exit_reason", "—"),
            )
        else:
            # Fallback: generic message
            text = tpl.generic_message(
                f"{event_type}: {symbol}",
                f"Entry ₹{entry_price:.2f} | SL ₹{stop_loss:.2f}",
                "info",
            )

        return await self._send(text)

    async def send_system_alert(
        self,
        event_type: NotificationEventType,
        message: str,
        payload: Optional[dict[str, Any]] = None,
    ) -> bool:
        payload = payload or {}

        if event_type == NotificationEventType.BROKER_DISCONNECTED:
            text = tpl.broker_disconnected(
                payload.get("broker", "unknown"),
                payload.get("reason", message),
            )
        elif event_type == NotificationEventType.WEBSOCKET_DISCONNECTED:
            text = tpl.websocket_disconnected(
                payload.get("feed", "unknown"),
                payload.get("reason", message),
            )
        elif event_type == NotificationEventType.SCHEDULER_FAILURE:
            text = tpl.scheduler_failure(
                payload.get("job_id", "unknown"),
                payload.get("error", message),
            )
        elif event_type == NotificationEventType.SYSTEM_ERROR:
            text = tpl.system_error(
                payload.get("component", "unknown"),
                payload.get("error", message),
                payload.get("detail", ""),
            )
        elif event_type == NotificationEventType.RECONCILIATION_MISMATCH:
            text = tpl.reconciliation_mismatch(
                payload.get("broker", "unknown"),
                payload.get("mismatch_count", 0),
                payload.get("description", message),
            )
        elif event_type == NotificationEventType.DATABASE_UNAVAILABLE:
            text = tpl.database_unavailable(payload.get("error", message))
        else:
            text = tpl.generic_message(str(event_type), message, "warning")

        return await self._send(text)

    async def send_incident_alert(
        self,
        incident_id: str,
        component: str,
        severity: str,
        title: str,
        description: str,
        status: str,
    ) -> bool:
        text = tpl.incident_alert(incident_id, component, severity, title, description, status)
        return await self._send(text)

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _send(self, text: str) -> bool:
        """
        Dispatch a single message to Telegram with retry + rate limiting.

        Returns True on success, False after exhausting retries.
        """
        if not self.is_enabled:
            logger.debug("Telegram notifier disabled — skipping send")
            return False

        await self._rate_limit()

        url = f"{_API_BASE}{settings.ALERT_TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": settings.ALERT_TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "MarkdownV2",
            "disable_web_page_preview": True,
        }

        last_error: str = ""
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(url, json=payload)
                    resp.raise_for_status()
                    import time
                    self._last_send_at = time.monotonic()
                    logger.debug("Telegram send OK (attempt %d)", attempt)
                    return True

            except httpx.HTTPStatusError as exc:
                last_error = f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
                # 429 Too Many Requests — back off longer
                if exc.response.status_code == 429:
                    retry_after = int(exc.response.headers.get("Retry-After", 10))
                    logger.warning("Telegram rate-limited; waiting %ds", retry_after)
                    await asyncio.sleep(retry_after)
                    continue
                # 4xx other than 429 are not retryable
                if exc.response.status_code < 500:
                    logger.error("Telegram send failed (non-retryable): %s", last_error)
                    return False

            except Exception as exc:
                last_error = str(exc)

            if attempt < _MAX_RETRIES:
                backoff = _BACKOFF_BASE * (2 ** (attempt - 1))
                logger.warning(
                    "Telegram send attempt %d/%d failed: %s — retrying in %.1fs",
                    attempt, _MAX_RETRIES, last_error, backoff,
                )
                await asyncio.sleep(backoff)

        logger.error(
            "Telegram send failed after %d attempts: %s", _MAX_RETRIES, last_error
        )
        return False

    async def _rate_limit(self) -> None:
        """Insert a micro-sleep if the last send was too recent."""
        import time
        elapsed = time.monotonic() - self._last_send_at
        if elapsed < _MIN_SEND_INTERVAL:
            await asyncio.sleep(_MIN_SEND_INTERVAL - elapsed)
