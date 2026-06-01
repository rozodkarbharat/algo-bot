"""
Notification infrastructure package.

Provider abstraction for Telegram, Email, and future channels (WhatsApp, Slack).
Entry point for external code is NotificationManager or, at a higher level,
NotificationService in app/services/notification_service.py.
"""

from app.notifications.base_notifier import BaseNotifier, NotificationEventType
from app.notifications.notification_manager import NotificationManager, notification_manager

__all__ = [
    "BaseNotifier",
    "NotificationEventType",
    "NotificationManager",
    "notification_manager",
]
