"""Build CompositeNotifier from YAML ``notify`` config + env credentials."""

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
    """Assemble alert channels from config.

    Gmail enables when channel ``enabled`` (default true for type=gmail) **and**
    ``GMAIL_APP_PASSWORD`` is present in the environment.
    """
    cfg = config or {}
    notify = cfg.get("notify") or {}
    channels_cfg = notify.get("channels")
    channels: list[Any] = [ConsoleNotifier()]

    if not isinstance(channels_cfg, list) or not channels_cfg:
        # Default: console + Gmail when password present
        gmail = GmailSMTPNotifier(enabled=None)
        if gmail.enabled:
            channels.append(gmail)
        return CompositeNotifier(channels)

    for ch in channels_cfg:
        if not isinstance(ch, dict):
            continue
        ctype = str(ch.get("type") or "").lower()
        enabled = bool(ch.get("enabled", False))
        if ctype == "console":
            if enabled or ch.get("enabled") is None:
                # Already have a ConsoleNotifier; skip duplicates
                continue
        elif ctype == "gmail":
            # Default enabled=true for gmail channel so password alone turns it on
            gmail_enabled = bool(ch.get("enabled", True))
            gmail = GmailSMTPNotifier(enabled=gmail_enabled if gmail_enabled else False)
            if gmail.enabled:
                channels.append(gmail)
            elif gmail_enabled:
                logger.info(
                    "Gmail channel enabled in config but GMAIL_APP_PASSWORD missing — skipped"
                )
        elif ctype == "telegram":
            channels.append(TelegramNotifier(enabled=enabled))
        elif ctype == "webhook":
            channels.append(WebhookNotifier(enabled=enabled))
        else:
            logger.debug("Unknown notify channel type=%s", ctype)

    # If config listed no gmail but password exists, still attach Gmail for dry-run week
    has_gmail = any(isinstance(c, GmailSMTPNotifier) for c in channels)
    if not has_gmail:
        auto = GmailSMTPNotifier(enabled=None)
        if auto.enabled:
            channels.append(auto)

    return CompositeNotifier(channels)


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
