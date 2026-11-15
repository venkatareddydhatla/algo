"""Build notifiers from YAML ``notify`` config + env credentials.

Gmail is **not** attached to the general CompositeNotifier used for scan/HOLD
console alerts. Email goes only via ``GmailSMTPNotifier.send_fill`` (paper
fills) and ``maybe_send_daily_summary`` (16:00 IST).
"""

from __future__ import annotations

import logging
from typing import Any

from options_portal_notify.gmail import GmailSMTPNotifier
from options_portal_notify.notifiers import (
    CompositeNotifier,
    ConsoleNotifier,
    TelegramNotifier,
    WebhookNotifier,
)

logger = logging.getLogger(__name__)


def build_notifier(config: dict[str, Any] | None = None) -> CompositeNotifier:
    """Assemble non-email alert channels (console / telegram / webhook).

    Gmail is excluded on purpose so HOLD and other tick chatter stay out of
    the inbox. Call ``build_gmail_notifier`` for fill/daily emails.
    """
    cfg = config or {}
    notify = cfg.get("notify") or {}
    channels_cfg = notify.get("channels")
    channels: list[Any] = [ConsoleNotifier()]

    if not isinstance(channels_cfg, list) or not channels_cfg:
        return CompositeNotifier(channels)

    for ch in channels_cfg:
        if not isinstance(ch, dict):
            continue
        ctype = str(ch.get("type") or "").lower()
        enabled = bool(ch.get("enabled", False))
        if ctype in {"console", "gmail"}:
            # console already present; gmail is fill/daily-only (see build_gmail_notifier)
            continue
        if ctype == "telegram":
            channels.append(TelegramNotifier(enabled=enabled))
        elif ctype == "webhook":
            channels.append(WebhookNotifier(enabled=enabled))
        else:
            logger.debug("Unknown notify channel type=%s", ctype)

    return CompositeNotifier(channels)


def build_gmail_notifier(config: dict[str, Any] | None = None) -> GmailSMTPNotifier | None:
    """Return a configured GmailSMTPNotifier when enabled + password present."""
    cfg = config or {}
    notify = cfg.get("notify") or {}
    channels_cfg = notify.get("channels")
    gmail_enabled = True
    if isinstance(channels_cfg, list):
        for ch in channels_cfg:
            if isinstance(ch, dict) and str(ch.get("type") or "").lower() == "gmail":
                gmail_enabled = bool(ch.get("enabled", True))
                break

    if not gmail_enabled:
        return None
    gmail = GmailSMTPNotifier(enabled=True)
    if not gmail.enabled:
        logger.info(
            "Gmail notify enabled in config but GMAIL_APP_PASSWORD missing — skipped"
        )
        return None
    return gmail


def gmail_from_notifier(notifier: Any) -> GmailSMTPNotifier | None:
    """Extract the first GmailSMTPNotifier from a Composite or return self."""
    if isinstance(notifier, GmailSMTPNotifier):
        return notifier
    channels = getattr(notifier, "channels", None)
    if not channels:
        return None
    for ch in channels:
        if isinstance(ch, GmailSMTPNotifier):
            return ch
    return None
