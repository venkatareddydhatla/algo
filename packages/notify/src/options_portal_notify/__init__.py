"""Notifier implementations (console / Gmail SMTP / webhook / telegram)."""

from options_portal_notify.daily_summary import (
    QUIET_SUMMARY_REASONS,
    daily_summary_config,
    maybe_send_daily_summary,
)
from options_portal_notify.factory import (
    build_gmail_notifier,
    build_notifier,
    gmail_from_notifier,
)
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
    "QUIET_SUMMARY_REASONS",
    "CompositeNotifier",
    "ConsoleNotifier",
    "GmailSMTPNotifier",
    "TelegramNotifier",
    "WebhookNotifier",
    "build_gmail_notifier",
    "build_notifier",
    "daily_summary_config",
    "gmail_from_notifier",
    "gmail_settings",
    "maybe_send_daily_summary",
    "send_gmail_message",
]
