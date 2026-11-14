"""Alert channels — credentials via env only."""

from __future__ import annotations

import logging
import os
from typing import Any, Sequence

import httpx

logger = logging.getLogger(__name__)


class ConsoleNotifier:
    async def send(self, title: str, body: str, meta: dict[str, Any]) -> None:
        logger.info("ALERT %s | %s | meta=%s", title, body, meta)


class WebhookNotifier:
    def __init__(self, url: str | None = None, *, enabled: bool = False) -> None:
        self.url = url or os.environ.get("ALERT_WEBHOOK_URL", "")
        self.enabled = enabled and bool(self.url)

    async def send(self, title: str, body: str, meta: dict[str, Any]) -> None:
        if not self.enabled:
            return
        payload = {"title": title, "body": body, "meta": meta}
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(self.url, json=payload)


class TelegramNotifier:
    def __init__(self, *, enabled: bool = False) -> None:
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        self.enabled = enabled and bool(self.token and self.chat_id)

    async def send(self, title: str, body: str, meta: dict[str, Any]) -> None:
        if not self.enabled:
            return
        text = f"*{title}*\n{body}"
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                url,
                json={"chat_id": self.chat_id, "text": text, "parse_mode": "Markdown"},
            )


class CompositeNotifier:
    def __init__(self, channels: Sequence[Any]) -> None:
        self.channels = list(channels)

    async def send(self, title: str, body: str, meta: dict[str, Any]) -> None:
        for ch in self.channels:
            try:
                await ch.send(title, body, meta)
            except Exception:  # noqa: BLE001
                logger.exception("Notifier %s failed", type(ch).__name__)
