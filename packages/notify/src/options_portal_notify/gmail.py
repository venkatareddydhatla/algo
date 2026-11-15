"""Gmail SMTP notifier — credentials from env only (never commit secrets).

Required: ``GMAIL_APP_PASSWORD`` (Google account app password).
Optional:
  ``GMAIL_USER`` — sender (defaults to ``NOTIFY_EMAIL_TO`` / default recipient)
  ``NOTIFY_EMAIL_TO`` — recipient (default ``venkatareddy.dhatla@gmail.com``)
"""

from __future__ import annotations

import html
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

# Soften long reason strings in table cells; full text kept via HTML title + plain appendix.
REASON_DISPLAY_MAX = 72

_ENTRY_ACTION_KINDS = frozenset({"entry", "flip_open"})
_EXIT_ACTION_KINDS = frozenset({"exit", "flip_close"})


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
    html_body: str | None = None,
    settings: GmailSettings | None = None,
    smtp_factory: Any = None,
) -> bool:
    """Send email via Gmail SMTP. Returns True on success.

    Always includes a plain-text part (``body``). When ``html_body`` is set,
    adds an ``text/html`` alternative (multipart/alternative).

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
    if html_body:
        msg.add_alternative(html_body, subtype="html")

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


def soften_reason(reason: str | None, *, max_len: int = REASON_DISPLAY_MAX) -> str:
    """Truncate a reason for table display; empty → em dash."""
    text = (reason or "").strip() or "—"
    if len(text) <= max_len:
        return text
    if max_len <= 1:
        return "…"
    return text[: max_len - 1].rstrip() + "…"


def _fill_sort_key(fill: dict[str, Any]) -> tuple[str, str]:
    return (
        str(fill.get("ts_ist") or fill.get("ts") or fill.get("ts_ist_display") or ""),
        str(fill.get("id") or ""),
    )


def _is_entry_fill(fill: dict[str, Any]) -> bool:
    """True for opens (entry / flip_open); False for closes.

    Falls back to realized_pnl when ``action_kind`` is missing (legacy rows).
    """
    kind = str(fill.get("action_kind") or "").strip().lower()
    if kind in _ENTRY_ACTION_KINDS:
        return True
    if kind in _EXIT_ACTION_KINDS:
        return False
    return fill.get("realized_pnl") is None


def _held_side_label(entry: dict[str, Any] | None, exit_fill: dict[str, Any]) -> str:
    """Map equity position to LONG/SHORT (Backtest CE≈long, PE≈short)."""
    if entry is not None:
        ps = str(entry.get("position_side") or "").strip().lower()
        if ps in {"long", "short"}:
            return ps.upper()
        order = str(entry.get("side") or "").strip().upper()
        if order == "BUY":
            return "LONG"
        if order == "SELL":
            return "SHORT"
    # Infer from closing order: SELL closes long, BUY covers short
    exit_side = str(exit_fill.get("side") or "").strip().upper()
    if exit_side == "SELL":
        return "LONG"
    if exit_side == "BUY":
        return "SHORT"
    ps = str(exit_fill.get("position_side") or "").strip().lower()
    if ps in {"long", "short"}:
        return ps.upper()
    return "—"


def pair_round_trip_trades(fills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pair paper ledger fills into closed round-trips (Backtest Fills layout).

    Chronological per-symbol: ``entry`` / ``flip_open`` opens a leg;
    ``exit`` / ``flip_close`` closes it into one row. Still-open legs are omitted
    (use the Open positions section — do not invent exits).
    """
    ordered = sorted(fills, key=_fill_sort_key)
    open_by_symbol: dict[str, dict[str, Any]] = {}
    closed: list[dict[str, Any]] = []
    for fill in ordered:
        symbol = str(fill.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        if _is_entry_fill(fill):
            open_by_symbol[symbol] = fill
            continue
        entry = open_by_symbol.pop(symbol, None)
        closed.append(
            {
                "symbol": symbol,
                "side": _held_side_label(entry, fill),
                "entry_ts_ist_display": (
                    str(entry.get("ts_ist_display") or "—") if entry else "—"
                ),
                "exit_ts_ist_display": str(fill.get("ts_ist_display") or "—"),
                "entry_price": entry.get("price") if entry else None,
                "exit_price": fill.get("price"),
                "pnl": fill.get("realized_pnl"),
                "reason": str(fill.get("reason") or "").strip(),
                "qty": fill.get("qty") if fill.get("qty") is not None else (
                    entry.get("qty") if entry else None
                ),
            }
        )
    return closed


def closed_trades_for_ist_day(
    trades: list[dict[str, Any]],
    day_ist: str,
) -> list[dict[str, Any]]:
    """Keep round-trips whose exit timestamp falls on ``YYYY-MM-DD`` (IST)."""
    prefix = (day_ist or "").strip()
    if not prefix:
        return list(trades)
    return [
        t
        for t in trades
        if str(t.get("exit_ts_ist_display") or "").startswith(prefix)
    ]


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _fmt_price(value: Any) -> str:
    try:
        return f"{float(value or 0):.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _fmt_pnl(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "—"


def _html_shell(title: str, body_inner: str) -> str:
    return (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
        f"<title>{_esc(title)}</title></head>"
        "<body style=\"margin:0;padding:16px;font-family:Segoe UI,Helvetica,Arial,sans-serif;"
        "font-size:14px;line-height:1.45;color:#1a1a1a;background:#ffffff;\">"
        f"{body_inner}"
        "</body></html>"
    )


def _html_kv_table(rows: list[tuple[str, str]]) -> str:
    cells = "".join(
        "<tr>"
        f"<td style=\"padding:4px 12px 4px 0;color:#555;white-space:nowrap;vertical-align:top;\">{_esc(k)}</td>"
        f"<td style=\"padding:4px 0;vertical-align:top;\"><strong>{_esc(v)}</strong></td>"
        "</tr>"
        for k, v in rows
    )
    return (
        "<table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" "
        f"style=\"border-collapse:collapse;margin:0 0 16px;\">{cells}</table>"
    )


def _html_data_table(headers: list[str], rows: list[list[tuple[str, str | None]]]) -> str:
    """Build a bordered data table. Each cell is (display, title_or_None)."""
    th = "".join(
        f"<th style=\"padding:8px 10px;text-align:left;background:#f3f4f6;border:1px solid #d1d5db;"
        f"font-weight:600;white-space:nowrap;\">{_esc(h)}</th>"
        for h in headers
    )
    body_rows: list[str] = []
    if not rows:
        body_rows.append(
            f"<tr><td colspan=\"{len(headers)}\" style=\"padding:10px;border:1px solid #d1d5db;"
            "color:#666;\">(none)</td></tr>"
        )
    else:
        for row in rows:
            tds = []
            for display, title in row:
                title_attr = f" title=\"{_esc(title)}\"" if title else ""
                tds.append(
                    f"<td style=\"padding:8px 10px;border:1px solid #d1d5db;vertical-align:top;"
                    f"max-width:280px;word-wrap:break-word;overflow-wrap:anywhere;\"{title_attr}>"
                    f"{_esc(display)}</td>"
                )
            body_rows.append("<tr>" + "".join(tds) + "</tr>")
    return (
        "<table role=\"table\" cellpadding=\"0\" cellspacing=\"0\" "
        "style=\"border-collapse:collapse;width:100%;max-width:920px;margin:0 0 20px;\">"
        f"<thead><tr>{th}</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody></table>"
    )


def _plain_kv(rows: list[tuple[str, str]]) -> list[str]:
    width = max((len(k) for k, _ in rows), default=0)
    return [f"{k + ':':<{width + 1}}  {v}" for k, v in rows]


def _plain_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    """Fixed-width-ish plain-text table (pipe separated for scanability)."""
    if not rows:
        return ["  (none)"]
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    sep = " | "
    header_line = sep.join(h.ljust(widths[i]) for i, h in enumerate(headers))
    rule = "-+-".join("-" * w for w in widths)
    out = [f"  {header_line}", f"  {rule}"]
    for row in rows:
        out.append("  " + sep.join(row[i].ljust(widths[i]) for i in range(len(headers))))
    return out


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
) -> tuple[str, str, str]:
    """Return (subject, plain_body, html_body) for a paper/dry-run fill notification."""
    subject = f"[dry-run] Fill {side} {qty} {symbol} @ {price:.2f}"
    reason_full = (reason or "").strip() or "—"
    reason_short = soften_reason(reason_full)
    kv = [
        ("Symbol", symbol or "—"),
        ("Side", side or "—"),
        ("Qty", str(qty)),
        ("Price", f"{price:.2f}"),
        ("Action", action_kind or "—"),
        ("Reason", reason_short),
        ("Time (IST)", ts_ist_display or "—"),
        ("Mode", mode),
        ("Execution", "dry_run (no live broker order)"),
    ]
    plain_lines = [
        "Options Portal — equity dry-run paper fill",
        "",
        *_plain_kv(kv),
        "",
    ]
    if reason_short != reason_full:
        plain_lines.extend([f"Full reason:  {reason_full}", ""])
    plain_lines.append("This is a simulated fill for the dry-run review week.")
    plain = "\n".join(plain_lines)

    reason_cell = (
        f"<span title=\"{_esc(reason_full)}\">{_esc(reason_short)}</span>"
        if reason_short != reason_full
        else _esc(reason_short)
    )
    # Rebuild HTML kv with reason allowing title hover (bypass _html_kv for that cell)
    html_rows = "".join(
        "<tr>"
        f"<td style=\"padding:4px 12px 4px 0;color:#555;white-space:nowrap;vertical-align:top;\">{_esc(k)}</td>"
        f"<td style=\"padding:4px 0;vertical-align:top;\"><strong>"
        f"{reason_cell if k == 'Reason' else _esc(v)}"
        f"</strong></td></tr>"
        for k, v in kv
    )
    html_inner = (
        "<h2 style=\"margin:0 0 12px;font-size:18px;\">Options Portal — equity dry-run paper fill</h2>"
        "<table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" "
        f"style=\"border-collapse:collapse;margin:0 0 16px;\">{html_rows}</table>"
        "<p style=\"margin:0;color:#555;font-size:13px;\">"
        "This is a simulated fill for the dry-run review week. "
        "Execution remains dry_run — no live Upstox orders.</p>"
    )
    return subject, plain, _html_shell(subject, html_inner)


def format_daily_summary_email(
    *,
    day_ist: str,
    day_realized: float,
    positions: list[dict[str, Any]],
    fills_today: list[dict[str, Any]] | None = None,
    fills: list[dict[str, Any]] | None = None,
    mode: str = "dry_run",
) -> tuple[str, str, str]:
    """Return (subject, plain_body, html_body) for the 16:00 IST daily dry-run summary.

    ``fills`` should be recent paper fills (not day-filtered) so overnight entries
    can pair with today's exits. ``fills_today`` is accepted for older callers and
    is used only when ``fills`` is omitted (day-scoped pairing only).
    """
    source_fills = list(fills if fills is not None else (fills_today or []))
    trades_today = closed_trades_for_ist_day(
        pair_round_trip_trades(source_fills),
        day_ist,
    )
    subject = f"[dry-run] Daily summary {day_ist} ({len(trades_today)} trades)"

    summary_kv = [
        ("Date (IST)", day_ist),
        ("Sent around", "16:00 IST"),
        ("Mode", mode),
        ("Day realized", f"{day_realized:.2f}"),
        ("Open symbols", str(len(positions))),
        ("Trades today", str(len(trades_today))),
    ]

    pos_headers = ["Symbol", "Side", "Qty", "Avg price"]
    pos_plain_rows: list[list[str]] = []
    pos_html_rows: list[list[tuple[str, str | None]]] = []
    for p in positions:
        symbol = str(p.get("symbol") or "—")
        side = str(p.get("side") or "—")
        qty = str(p.get("qty") if p.get("qty") is not None else "—")
        avg = _fmt_price(p.get("avg_price"))
        pos_plain_rows.append([symbol, side, qty, avg])
        pos_html_rows.append(
            [(symbol, None), (side, None), (qty, None), (avg, None)]
        )

    # Backtest Fills layout: Side, Entry (IST), Exit (IST), Entry px, Exit px, PnL, Reason.
    # Symbol added (multi-name equity book). Bars omitted (paper ledger is event-based).
    trade_headers = [
        "Symbol",
        "Side",
        "Entry (IST)",
        "Exit (IST)",
        "Entry px",
        "Exit px",
        "PnL",
        "Reason",
    ]
    trade_plain_rows: list[list[str]] = []
    trade_html_rows: list[list[tuple[str, str | None]]] = []
    full_reasons: list[tuple[str, str]] = []
    for t in trades_today:
        symbol = str(t.get("symbol") or "—")
        side = str(t.get("side") or "—")
        entry_ts = str(t.get("entry_ts_ist_display") or "—")
        exit_ts = str(t.get("exit_ts_ist_display") or "—")
        entry_px = _fmt_price(t.get("entry_price")) if t.get("entry_price") is not None else "—"
        exit_px = _fmt_price(t.get("exit_price")) if t.get("exit_price") is not None else "—"
        pnl = _fmt_pnl(t.get("pnl"))
        reason_full = str(t.get("reason") or "").strip() or "—"
        reason_short = soften_reason(reason_full)
        trade_plain_rows.append(
            [symbol, side, entry_ts, exit_ts, entry_px, exit_px, pnl, reason_short]
        )
        title = reason_full if reason_short != reason_full else None
        trade_html_rows.append(
            [
                (symbol, None),
                (side, None),
                (entry_ts, None),
                (exit_ts, None),
                (entry_px, None),
                (exit_px, None),
                (pnl, None),
                (reason_short, title),
            ]
        )
        if title:
            full_reasons.append((f"{symbol} {side} {exit_ts}", reason_full))

    plain_lines = [
        "Options Portal — equity dry-run daily summary",
        "",
        *_plain_kv(summary_kv),
        "",
        "Open positions:",
        *_plain_table(pos_headers, pos_plain_rows),
        "",
        "Today's trades:",
        *_plain_table(trade_headers, trade_plain_rows),
        "",
    ]
    if full_reasons:
        plain_lines.append("Full reasons (truncated above):")
        for label, reason in full_reasons:
            plain_lines.append(f"  - {label}: {reason}")
        plain_lines.append("")
    plain_lines.append("Execution remains dry_run — no live Upstox orders.")
    plain = "\n".join(plain_lines)

    html_inner = (
        "<h2 style=\"margin:0 0 12px;font-size:18px;\">Options Portal — equity dry-run daily summary</h2>"
        + _html_kv_table(summary_kv)
        + "<h3 style=\"margin:0 0 8px;font-size:15px;\">Open positions</h3>"
        + _html_data_table(pos_headers, pos_html_rows)
        + "<h3 style=\"margin:0 0 8px;font-size:15px;\">Today's trades</h3>"
        + "<p style=\"margin:0 0 8px;color:#555;font-size:13px;\">"
        "One row per closed round-trip (entry + exit). "
        "Side is LONG/SHORT (equity; Backtest CE≈long, PE≈short). "
        "Still-open legs stay under Open positions.</p>"
        + _html_data_table(trade_headers, trade_html_rows)
        + "<p style=\"margin:0;color:#555;font-size:13px;\">"
        "Execution remains dry_run — no live Upstox orders. "
        "Hover a truncated Reason cell for the full text.</p>"
    )
    return subject, plain, _html_shell(subject, html_inner)


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
        """CompositeNotifier hook — intentionally a no-op.

        Gmail is **fill + daily summary only**. HOLD / neutral / scan ticks must
        never email. Use ``send_fill`` or ``send_daily_summary`` instead.
        """
        _ = (title, body, meta)
        if self.enabled:
            logger.debug(
                "GmailSMTPNotifier.send ignored (fill/daily only): %s", title
            )
        return

    def send_fill(self, fill: dict[str, Any], *, mode: str = "dry_run") -> bool:
        """Synchronous fill email (used from pipeline after paper persist)."""
        if not self.enabled:
            return False
        subject, body, html_body = format_fill_email(
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
            html_body=html_body,
            settings=self._settings,
            smtp_factory=self._smtp_factory,
        )

    def send_daily_summary(
        self,
        *,
        day_ist: str,
        day_realized: float,
        positions: list[dict[str, Any]],
        fills_today: list[dict[str, Any]] | None = None,
        fills: list[dict[str, Any]] | None = None,
        mode: str = "dry_run",
    ) -> bool:
        if not self.enabled:
            return False
        subject, body, html_body = format_daily_summary_email(
            day_ist=day_ist,
            day_realized=day_realized,
            positions=positions,
            fills_today=fills_today,
            fills=fills,
            mode=mode,
        )
        return send_gmail_message(
            subject=subject,
            body=body,
            html_body=html_body,
            settings=self._settings,
            smtp_factory=self._smtp_factory,
        )
