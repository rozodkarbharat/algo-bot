"""
Alert service — dispatches notifications on critical trading events.

Channels:
  - Telegram : async HTTP POST to Bot API (uses existing httpx client)
  - Email    : SMTP via stdlib smtplib in a thread-pool executor
  - System   : log-only (always active, no external calls)

All sends are best-effort: failures are recorded in the AlertEvent document
but never raised to the caller. The service deduplicate bursts: the same
event_type will not re-send within DEDUP_WINDOW_SECONDS if a record exists.

Usage:
    await alert_service.send(
        event_type="signal_generated",
        severity=AlertSeverity.INFO,
        title="BUY signal: RELIANCE",
        body="ORB breakout at ₹2540.00 — SL ₹2490.00 (prob 71%)",
        payload={"symbol": "RELIANCE", "entry": 2540.0},
    )
"""

import asyncio
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Optional

import httpx

from app.config.settings import settings
from app.models.alert_event import AlertEvent, AlertSeverity, AlertChannel
from app.repositories.alert_event_repository import AlertEventRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Burst dedup window: same dedup_key won't re-fire within this many seconds
DEDUP_WINDOW_SECONDS = 300


class AlertService:
    def __init__(self) -> None:
        self._repo = AlertEventRepository()

    # ── Public interface ──────────────────────────────────────────────────────

    async def send(
        self,
        event_type: str,
        title: str,
        body: str,
        severity: AlertSeverity = AlertSeverity.INFO,
        payload: Optional[dict[str, Any]] = None,
        dedup_key: Optional[str] = None,
        channel: Optional[AlertChannel] = None,
    ) -> None:
        """Dispatch an alert. Never raises."""
        try:
            # Dedup check
            dk = dedup_key or event_type
            existing = await self._repo.find_recent_by_dedup_key(dk, DEDUP_WINDOW_SECONDS)
            if existing:
                logger.debug(
                    "Alert suppressed (dedup): %s within %ds window", event_type, DEDUP_WINDOW_SECONDS
                )
                return

            # Determine effective channel
            effective_channel = channel or self._default_channel()

            event = AlertEvent(
                event_type=event_type,
                severity=severity,
                title=title,
                body=body,
                payload=payload,
                channel=effective_channel,
                dedup_key=dk,
            )
            await event.insert()

            # Dispatch
            success, error = await self._dispatch(event, effective_channel)
            from datetime import datetime, timezone
            event.delivered = success
            event.delivered_at = datetime.now(timezone.utc) if success else None
            event.delivery_error = error
            await event.save()

        except Exception as exc:
            logger.error("AlertService.send failed for %s: %s", event_type, exc)

    # ── Convenience wrappers ──────────────────────────────────────────────────

    async def signal_generated(self, symbol: str, side: str, entry: float, sl: float, prob: float) -> None:
        await self.send(
            event_type="signal_generated",
            title=f"{side} Signal: {symbol}",
            body=f"ORB breakout — Entry ₹{entry:.2f} | SL ₹{sl:.2f} | Prob {prob*100:.1f}%",
            severity=AlertSeverity.INFO,
            payload={"symbol": symbol, "side": side, "entry": entry, "sl": sl, "prob": prob},
            dedup_key=f"signal:{symbol}:{side}",
        )

    async def sl_hit(self, symbol: str, side: str, sl: float, pnl: float) -> None:
        await self.send(
            event_type="sl_hit",
            title=f"Stop Loss Hit: {symbol}",
            body=f"{side} position stopped out at ₹{sl:.2f} | PnL: ₹{pnl:+.2f}",
            severity=AlertSeverity.WARNING,
            payload={"symbol": symbol, "side": side, "sl": sl, "pnl": pnl},
            dedup_key=f"sl_hit:{symbol}",
        )

    async def broker_disconnected(self, broker: str, reason: str) -> None:
        await self.send(
            event_type="broker_disconnected",
            title=f"Broker Disconnected: {broker}",
            body=f"Lost connection to {broker}. Reason: {reason}",
            severity=AlertSeverity.CRITICAL,
            payload={"broker": broker, "reason": reason},
            dedup_key=f"broker_disconnected:{broker}",
        )

    async def daily_loss_limit(self, current_loss: float, limit: float) -> None:
        await self.send(
            event_type="daily_loss_limit",
            title="Daily Loss Limit Reached",
            body=f"Current loss ₹{abs(current_loss):.2f} exceeded limit ₹{limit:.2f}. Trading paused.",
            severity=AlertSeverity.CRITICAL,
            payload={"current_loss": current_loss, "limit": limit},
            dedup_key="daily_loss_limit",
        )

    async def scheduler_failure(self, job_id: str, error: str) -> None:
        await self.send(
            event_type="scheduler_failure",
            title=f"Scheduler Job Failed: {job_id}",
            body=f"Job '{job_id}' raised an exception: {error}",
            severity=AlertSeverity.CRITICAL,
            payload={"job_id": job_id, "error": error},
            dedup_key=f"scheduler_failure:{job_id}",
        )

    async def system_error(self, component: str, error: str) -> None:
        await self.send(
            event_type="system_error",
            title=f"System Error: {component}",
            body=error,
            severity=AlertSeverity.CRITICAL,
            payload={"component": component, "error": error},
            dedup_key=f"system_error:{component}",
        )

    # ── Internal dispatch ─────────────────────────────────────────────────────

    def _default_channel(self) -> AlertChannel:
        if settings.ALERT_TELEGRAM_ENABLED:
            return AlertChannel.TELEGRAM
        if settings.ALERT_EMAIL_ENABLED:
            return AlertChannel.EMAIL
        return AlertChannel.SYSTEM

    async def _dispatch(self, event: AlertEvent, channel: AlertChannel) -> tuple[bool, Optional[str]]:
        logger.info(
            "Alert [%s] %s: %s",
            event.severity.upper(),
            event.event_type,
            event.title,
        )

        if channel == AlertChannel.SYSTEM:
            return True, None

        if channel == AlertChannel.TELEGRAM and settings.ALERT_TELEGRAM_ENABLED:
            return await self._send_telegram(event)

        if channel == AlertChannel.EMAIL and settings.ALERT_EMAIL_ENABLED:
            return await self._send_email(event)

        return True, None

    async def _send_telegram(self, event: AlertEvent) -> tuple[bool, Optional[str]]:
        if not settings.ALERT_TELEGRAM_BOT_TOKEN or not settings.ALERT_TELEGRAM_CHAT_ID:
            return False, "Telegram credentials not configured"
        try:
            severity_emoji = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}.get(
                event.severity, "📢"
            )
            text = (
                f"{severity_emoji} *{event.title}*\n\n"
                f"{event.body}\n\n"
                f"`{event.event_type}` | `{event.timestamp.strftime('%H:%M:%S IST')}`"
            )
            url = f"https://api.telegram.org/bot{settings.ALERT_TELEGRAM_BOT_TOKEN}/sendMessage"
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    url,
                    json={
                        "chat_id": settings.ALERT_TELEGRAM_CHAT_ID,
                        "text": text,
                        "parse_mode": "Markdown",
                    },
                )
                resp.raise_for_status()
            logger.info("Telegram alert sent: %s", event.event_type)
            return True, None
        except Exception as exc:
            logger.error("Telegram send failed: %s", exc)
            return False, str(exc)

    async def _send_email(self, event: AlertEvent) -> tuple[bool, Optional[str]]:
        if not settings.ALERT_EMAIL_FROM or not settings.ALERT_EMAIL_TO:
            return False, "Email credentials not configured"
        try:
            # Run blocking SMTP in thread pool
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._smtp_send, event)
            logger.info("Email alert sent: %s", event.event_type)
            return True, None
        except Exception as exc:
            logger.error("Email send failed: %s", exc)
            return False, str(exc)

    def _smtp_send(self, event: AlertEvent) -> None:
        recipients = [r.strip() for r in settings.ALERT_EMAIL_TO.split(",") if r.strip()]
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[TradingBot] {event.severity.upper()}: {event.title}"
        msg["From"] = settings.ALERT_EMAIL_FROM
        msg["To"] = ", ".join(recipients)

        html = (
            f"<h3>{event.title}</h3>"
            f"<p>{event.body}</p>"
            f"<hr><small>Event: {event.event_type} | "
            f"Time: {event.timestamp.isoformat()}</small>"
        )
        msg.attach(MIMEText(event.body, "plain"))
        msg.attach(MIMEText(html, "html"))

        context = ssl.create_default_context()
        with smtplib.SMTP(settings.ALERT_EMAIL_SMTP_HOST, settings.ALERT_EMAIL_SMTP_PORT) as srv:
            srv.starttls(context=context)
            srv.login(settings.ALERT_EMAIL_SMTP_USER, settings.ALERT_EMAIL_SMTP_PASSWORD)
            srv.sendmail(settings.ALERT_EMAIL_FROM, recipients, msg.as_string())


alert_service = AlertService()
