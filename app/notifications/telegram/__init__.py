"""
app/notifications/telegram — Telegram Bot notification provider package.

Re-exports TelegramNotifier and the template module so callers can import
from either the package or the legacy flat module path:

    # Package path (preferred)
    from app.notifications.telegram import TelegramNotifier
    from app.notifications.telegram import templates

    # Legacy flat path (still works)
    from app.notifications.telegram_notifier import TelegramNotifier
"""

from app.notifications.telegram_notifier import TelegramNotifier
from app.notifications.templates import telegram_templates as templates

__all__ = ["TelegramNotifier", "templates"]
