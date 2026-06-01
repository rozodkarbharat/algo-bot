"""
app/notifications/email — SMTP email notification provider package.

Re-exports EmailNotifier and the template module so callers can import
from either the package or the legacy flat module path:

    # Package path (preferred)
    from app.notifications.email import EmailNotifier
    from app.notifications.email import templates

    # Legacy flat path (still works)
    from app.notifications.email_notifier import EmailNotifier
"""

from app.notifications.email_notifier import EmailNotifier
from app.notifications.templates import email_templates as templates

__all__ = ["EmailNotifier", "templates"]
