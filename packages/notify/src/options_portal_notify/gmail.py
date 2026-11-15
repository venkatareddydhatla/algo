"""Gmail SMTP notifier — credentials from env only (never commit secrets).

Required: ``GMAIL_APP_PASSWORD`` (Google account app password).
Optional:
  ``GMAIL_USER`` — sender (defaults to ``NOTIFY_EMAIL_TO`` / default recipient)
  ``NOTIFY_EMAIL_TO`` — recipient (default ``venkatareddy.dhatla@gmail.com``)
"""

from __future__ import annotations

import logging
import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_NOTIFY_EMAIL_TO = "venkatareddy.dhatla@gmail.com"
GMAIL_SMTP_HOST = "smtp.gmail.com"
GMAIL_SMTP_PORT = 587


@dataclass(frozen=True)
class GmailSettings:
    """Resolved Gmail SMTP settings (password never logged)."""

    user: str
    password: str
    to_addr: str
    enabled: bool

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.user and self.password and self.to_addr)


def gmail_settings(*, enabled: bool | None = None) -> GmailSettings:
    """Read Gmail settings from environment.

    When ``enabled`` is None, enable automatically if ``GMAIL_APP_PASSWORD`` is set.
    """
    password = (os.environ.get("GMAIL_APP_PASSWORD") or "").strip()
    to_addr = (
        os.environ.get("NOTIFY_EMAIL_TO") or DEFAULT_NOTIFY_EMAIL_TO
    ).strip() or DEFAULT_NOTIFY_EMAIL_TO
    user = (os.environ.get("GMAIL_USER") or to_addr).strip() or to_addr
    if enabled is None:
        enabled = bool(password)
    return GmailSettings(
        user=user,
        password=password,
        to_addr=to_addr,
        enabled=bool(enabled) and bool(password),
    )


def send_gmail_message(
    *,
    subject: str,
    body: str,
    settings: GmailSettings | None = None,
    smtp_factory: Any = None,
) -> bool:
    """Send a plain-text email via Gmail SMTP. Returns True on success.

    ``smtp_factory`` is injectable for tests (defaults to ``smtplib.SMTP``).
    """
    cfg = settings or gmail_settings()
    if not cfg.configured:
        logger.warning(
            "Gmail notify skipped: set GMAIL_APP_PASSWORD in local .env "
            "(optional GMAIL_USER / NOTIFY_EMAIL_TO). Cloud agents cannot send "
            "without your app password."
        )
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.user
    msg["To"] = cfg.to_addr
    msg.set_content(body)

    factory = smtp_factory or smtplib.SMTP
    try:
        with factory(GMAIL_SMTP_HOST, GMAIL_SMTP_PORT, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(cfg.user, cfg.password)
            smtp.send_message(msg)
    except Exception:  # noqa: BLE001
        logger.exception("Gmail SMTP send failed (to=%s)", cfg.to_addr)
        return False
    logger.info("Gmail sent subject=%r to=%s", subject, cfg.to_addr)
    return True


def format_fill_email(
    *,
    symbol: str,
    side: str,
    qty: int,
    price: float,
    reason: str,
    ts_ist_display: str | None,
    action_kind: str | None = None,
    mode: str = "dry_run",
) -> tuple[str, str]:
    """Return (subject, body) for a paper/dry-run fill notification."""
    subject = f"[dry-run] Fill {side} {qty} {symbol} @ {price:.2f}"
    lines = [
        "Options Portal — equity dry-run paper fill",
        "",
        f"Symbol:      {symbol}",
        f"Side:        {side}",
        f"Qty:         {qty}",
        f"Price:       {price:.2f}",
        f"Action:      {action_kind or '—'}",
        f"Reason:      {reason or '—'}",
        f"Time (IST):  {ts_ist_display or '—'}",
        f"Mode:        {mode}",
        "Execution:   dry_run (no live broker order)",
        "",
        "This is a simulated fill for the dry-run review week.",
    ]
    return subject, "\n".join(lines)


def format_daily_summary_email(
    *,
    day_ist: str,
    day_realized: float,
    positions: list[dict[str, Any]],
    fills_today: list[dict[str, Any]],
    mode: str = "dry_run",
) -> tuple[str, str]:
    """Return (subject, body) for the 16:00 IST daily dry-run summary."""
    subject = f"[dry-run] Daily summary {day_ist} ({len(fills_today)} fills)"
    pos_lines: list[str] = []
    if positions:
        for p in positions:
            pos_lines.append(
                f"  - {p.get('symbol')} {p.get('side')} qty={p.get('qty')} "
                f"avg={float(p.get('avg_price') or 0):.2f}"
            )
    else:
        pos_lines.append("  (none)")

    fill_lines: list[str] = []
    if fills_today:
        for f in fills_today:
            pnl = f.get("realized_pnl")
            pnl_s = f" pnl={float(pnl):.2f}" if pnl is not None else ""
            fill_lines.append(
                f"  - {f.get('ts_ist_display', '—')} {f.get('side')} "
                f"{f.get('qty')} {f.get('symbol')} @ {float(f.get('price') or 0):.2f}"
                f"{pnl_s} ({f.get('reason') or '—'})"
            )
    else:
        fill_lines.append("  (no fills today)")

    lines = [
        "Options Portal — equity dry-run daily summary",
        f"Date (IST):     {day_ist}",
        f"Sent around:    16:00 IST",
        f"Mode:           {mode}",
        f"Day realized:   {day_realized:.2f}",
        f"Open symbols:   {len(positions)}",
        f"Fills today:    {len(fills_today)}",
        "",
        "Open positions:",
        *pos_lines,
        "",
        "Today's fills:",
        *fill_lines,
        "",
        "Execution remains dry_run — no live Upstox orders.",
    ]
    return subject, "\n".join(lines)


class GmailSMTPNotifier:
    """Async-compatible notifier that sends via Gmail SMTP (thread offload)."""

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        settings: GmailSettings | None = None,
        smtp_factory: Any = None,
    ) -> None:
        self._settings = settings or gmail_settings(enabled=enabled)
        self._smtp_factory = smtp_factory
        self.enabled = self._settings.configured

    async def send(self, title: str, body: str, meta: dict[str, Any]) -> None:
        if not self.enabled:
            return
        import asyncio

        subject = title if title.startswith("[") else f"[dry-run] {title}"
        meta_bits = []
        if meta:
            for key in ("symbol", "side", "qty", "price", "ts_ist_display"):
                if key in meta and meta[key] is not None:
                    meta_bits.append(f"{key}={meta[key]}")
        full_body = body
        if meta_bits:
            full_body = f"{body}\n\nmeta: {', '.join(meta_bits)}"
        await asyncio.to_thread(
            send_gmail_message,
            subject=subject,
            body=full_body,
            settings=self._settings,
            smtp_factory=self._smtp_factory,
        )

    def send_fill(self, fill: dict[str, Any], *, mode: str = "dry_run") -> bool:
        """Synchronous fill email (used from pipeline after paper persist)."""
        if not self.enabled:
            return False
        subject, body = format_fill_email(
            symbol=str(fill.get("symbol") or ""),
            side=str(fill.get("side") or ""),
            qty=int(fill.get("qty") or 0),
            price=float(fill.get("price") or 0.0),
            reason=str(fill.get("reason") or ""),
            ts_ist_display=fill.get("ts_ist_display"),
            action_kind=fill.get("action_kind"),
            mode=mode,
        )
        return send_gmail_message(
            subject=subject,
            body=body,
            settings=self._settings,
            smtp_factory=self._smtp_factory,
        )

    def send_daily_summary(
        self,
        *,
        day_ist: str,
        day_realized: float,
        positions: list[dict[str, Any]],
        fills_today: list[dict[str, Any]],
        mode: str = "dry_run",
    ) -> bool:
        if not self.enabled:
            return False
        subject, body = format_daily_summary_email(
            day_ist=day_ist,
            day_realized=day_realized,
            positions=positions,
            fills_today=fills_today,
            mode=mode,
        )
        return send_gmail_message(
            subject=subject,
            body=body,
            settings=self._settings,
            smtp_factory=self._smtp_factory,
        )
