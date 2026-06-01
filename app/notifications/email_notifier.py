"""
SMTP email notification provider.

Implements BaseNotifier over standard-library smtplib, run in a thread-pool
executor to avoid blocking the asyncio event loop (SMTP is synchronous).

Features:
  - HTML + plain-text multipart emails via email_templates.py
  - TLS via STARTTLS (port 587 default)
  - Exponential back-off retry (max 3 attempts: 2s, 4s, 8s)
  - Multiple recipients: ALERT_EMAIL_TO may be a comma-separated list
  - Best-effort: failures logged but never raised to caller
"""

import asyncio
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Optional

from app.config.settings import settings
from app.notifications.base_notifier import AlertSeverity, BaseNotifier, NotificationEventType
from app.notifications.templates import email_templates as tpl
from app.utils.logger import get_logger

logger = get_logger(__name__)

_MAX_RETRIES = 3
_BACKOFF_BASE = 2.0


class EmailNotifier(BaseNotifier):
    """
    Notification provider that sends HTML emails over SMTP.
    """

    # ── BaseNotifier interface ────────────────────────────────────────────────

    @property
    def channel_name(self) -> str:
        return "email"

    @property
    def is_enabled(self) -> bool:
        return (
            settings.ALERT_EMAIL_ENABLED
            and bool(settings.ALERT_EMAIL_FROM)
            and bool(settings.ALERT_EMAIL_TO)
            and bool(settings.ALERT_EMAIL_SMTP_USER)
        )

    async def send_message(
        self,
        title: str,
        body: str,
        severity: AlertSeverity = AlertSeverity.INFO,
        payload: Optional[dict[str, Any]] = None,
    ) -> bool:
        subject, plain, html = tpl.generic_message(title, body, severity)
        return await self._send(subject, plain, html)

    async def send_error(
        self,
        component: str,
        error: str,
        detail: str = "",
    ) -> bool:
        subject, plain, html = tpl.system_error(component, error, detail)
        return await self._send(subject, plain, html)

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
        date_str = extra.get("trading_date", "")

        if event_type in (
            NotificationEventType.PAPER_TRADE_ENTERED,
            NotificationEventType.LIVE_TRADE_ENTERED,
        ):
            capital_used = entry_price * quantity
            subject, plain, html = tpl.trade_entered(
                mode, symbol, side, entry_price, stop_loss, quantity, capital_used, date_str
            )

        elif event_type == NotificationEventType.STOP_LOSS_HIT:
            subject, plain, html = tpl.stop_loss_hit(mode, symbol, side, stop_loss, pnl or 0.0)

        elif event_type in (
            NotificationEventType.PAPER_TRADE_EXITED,
            NotificationEventType.LIVE_TRADE_EXITED,
        ):
            subject, plain, html = tpl.trade_exited(
                mode,
                symbol,
                side,
                entry_price,
                extra.get("exit_price", entry_price),
                quantity,
                pnl or 0.0,
                extra.get("exit_reason", "—"),
                date_str,
            )
        else:
            subject, plain, html = tpl.generic_message(
                f"{event_type}: {symbol}",
                f"Entry ₹{entry_price:.2f} | SL ₹{stop_loss:.2f}",
                "info",
            )

        return await self._send(subject, plain, html)

    async def send_system_alert(
        self,
        event_type: NotificationEventType,
        message: str,
        payload: Optional[dict[str, Any]] = None,
    ) -> bool:
        payload = payload or {}

        if event_type == NotificationEventType.BROKER_DISCONNECTED:
            subject, plain, html = tpl.broker_disconnected(
                payload.get("broker", "unknown"),
                payload.get("reason", message),
            )
        elif event_type == NotificationEventType.SCHEDULER_FAILURE:
            subject, plain, html = tpl.scheduler_failure(
                payload.get("job_id", "unknown"),
                payload.get("error", message),
            )
        elif event_type in (
            NotificationEventType.SYSTEM_ERROR,
            NotificationEventType.WEBSOCKET_DISCONNECTED,
        ):
            subject, plain, html = tpl.system_error(
                payload.get("component", str(event_type)),
                payload.get("error", message),
                payload.get("detail", ""),
            )
        elif event_type == NotificationEventType.RECONCILIATION_MISMATCH:
            subject, plain, html = tpl.reconciliation_mismatch(
                payload.get("broker", "unknown"),
                payload.get("mismatch_count", 0),
                payload.get("description", message),
            )
        elif event_type == NotificationEventType.DATABASE_UNAVAILABLE:
            subject, plain, html = tpl.database_unavailable(payload.get("error", message))
        else:
            subject, plain, html = tpl.generic_message(str(event_type), message, "warning")

        return await self._send(subject, plain, html)

    async def send_incident_alert(
        self,
        incident_id: str,
        component: str,
        severity: str,
        title: str,
        description: str,
        status: str,
    ) -> bool:
        subject, plain, html = tpl.incident_alert(
            incident_id, component, severity, title, description, status
        )
        return await self._send(subject, plain, html)

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _send(self, subject: str, plain: str, html: str) -> bool:
        if not self.is_enabled:
            logger.debug("Email notifier disabled — skipping send")
            return False

        last_error: str = ""
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._smtp_send, subject, plain, html)
                logger.debug("Email send OK (attempt %d)", attempt)
                return True

            except Exception as exc:
                last_error = str(exc)
                if attempt < _MAX_RETRIES:
                    backoff = _BACKOFF_BASE * (2 ** (attempt - 1))
                    logger.warning(
                        "Email send attempt %d/%d failed: %s — retrying in %.1fs",
                        attempt, _MAX_RETRIES, last_error, backoff,
                    )
                    await asyncio.sleep(backoff)

        logger.error("Email send failed after %d attempts: %s", _MAX_RETRIES, last_error)
        return False

    def _smtp_send(self, subject: str, plain: str, html: str) -> None:
        """Blocking SMTP send — must be called via run_in_executor."""
        recipients = [
            r.strip()
            for r in settings.ALERT_EMAIL_TO.split(",")
            if r.strip()
        ]
        if not recipients:
            raise ValueError("No valid recipients in ALERT_EMAIL_TO")

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.ALERT_EMAIL_FROM
        msg["To"] = ", ".join(recipients)

        msg.attach(MIMEText(plain, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))

        context = ssl.create_default_context()
        with smtplib.SMTP(settings.ALERT_EMAIL_SMTP_HOST, settings.ALERT_EMAIL_SMTP_PORT) as srv:
            srv.ehlo()
            srv.starttls(context=context)
            srv.ehlo()
            srv.login(settings.ALERT_EMAIL_SMTP_USER, settings.ALERT_EMAIL_SMTP_PASSWORD)
            srv.sendmail(settings.ALERT_EMAIL_FROM, recipients, msg.as_string())
        logger.info("Email sent: %s → %s", subject, recipients)
