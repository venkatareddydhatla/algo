"""Notifier implementations (console / Gmail SMTP / webhook / telegram)."""

from options_portal_notify.daily_summary import maybe_send_daily_summary
from options_portal_notify.factory import build_notifier, gmail_from_notifier
from options_portal_notify.gmail import (
    DEFAULT_NOTIFY_EMAIL_TO,
    GmailSMTPNotifier,
    gmail_settings,
    send_gmail_message,
)
from options_portal_notify.notifiers import (
    ConsoleNotifier,
    CompositeNotifier,
    TelegramNotifier,
    WebhookNotifier,
)

__all__ = [
    "DEFAULT_NOTIFY_EMAIL_TO",
    "CompositeNotifier",
    "ConsoleNotifier",
    "GmailSMTPNotifier",
    "TelegramNotifier",
    "WebhookNotifier",
    "build_notifier",
    "gmail_from_notifier",
    "gmail_settings",
    "maybe_send_daily_summary",
    "send_gmail_message",
]
