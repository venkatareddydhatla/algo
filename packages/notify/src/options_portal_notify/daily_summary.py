"""Daily dry-run email summary at 16:00 IST (once per calendar day)."""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from options_portal_domain.calendar import is_nse_trading_day
from options_portal_domain.tz import ist, now_ist
from options_portal_notify.gmail import GmailSMTPNotifier, gmail_settings

logger = logging.getLogger(__name__)

DEFAULT_HOUR_IST = 16
DEFAULT_MINUTE_IST = 0
# Independent of full-universe scan duration — poll often enough to hit ~16:00 IST.
DEFAULT_POLL_SEC = 30
_STATE_FILENAME = "notify_daily_summary_state.json"
# Reasons that are routine (do not INFO-log on every poll).
QUIET_SUMMARY_REASONS = frozenset({"not_due", "disabled", "non_trading_day"})


def daily_summary_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    notify = (config or {}).get("notify") or {}
    raw = notify.get("daily_summary") or {}
    poll = raw.get("poll_interval_sec", DEFAULT_POLL_SEC)
    env_poll = os.environ.get("DAILY_SUMMARY_POLL_SEC")
    if env_poll is not None and str(env_poll).strip() != "":
        poll = env_poll
    return {
        "enabled": bool(raw.get("enabled", True)),
        "hour_ist": int(raw.get("hour_ist", DEFAULT_HOUR_IST)),
        "minute_ist": int(raw.get("minute_ist", DEFAULT_MINUTE_IST)),
        "poll_interval_sec": max(5, int(poll)),
    }


def state_path() -> Path:
    """Marker file under data/ (or NOTIFY_STATE_DIR) so restarts do not double-send."""
    base = Path(os.environ.get("NOTIFY_STATE_DIR") or os.environ.get("DATA_DIR") or "data")
    return base / _STATE_FILENAME


def load_last_sent_day(path: Path | None = None) -> date | None:
    p = path or state_path()
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        raw = data.get("last_sent_ist_day")
        if not raw:
            return None
        return date.fromisoformat(str(raw))
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def save_last_sent_day(day: date, path: Path | None = None) -> None:
    p = path or state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"last_sent_ist_day": day.isoformat()}
    p.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def should_send_daily_summary(
    now: datetime | None = None,
    *,
    hour_ist: int = DEFAULT_HOUR_IST,
    minute_ist: int = DEFAULT_MINUTE_IST,
    last_sent: date | None = None,
) -> bool:
    """True when IST clock is at/after the schedule and we have not sent today."""
    current = now or now_ist()
    if current.tzinfo is None:
        current = current.replace(tzinfo=ist())
    else:
        current = current.astimezone(ist())
    today = current.date()
    if last_sent is not None and last_sent >= today:
        return False
    threshold = datetime.combine(today, time(hour_ist, minute_ist), tzinfo=ist())
    return current >= threshold


def fills_for_ist_day(
    fills: list[dict[str, Any]],
    day: date | None = None,
) -> list[dict[str, Any]]:
    """Filter fill rows whose ``ts_ist_display`` starts with ``YYYY-MM-DD``."""
    target = day or now_ist().date()
    prefix = target.isoformat()
    out: list[dict[str, Any]] = []
    for f in fills:
        disp = str(f.get("ts_ist_display") or "")
        if disp.startswith(prefix):
            out.append(f)
    return out


def maybe_send_daily_summary(
    *,
    config: dict[str, Any] | None = None,
    gmail: GmailSMTPNotifier | None = None,
    paper_store: Any = None,
    now: datetime | None = None,
    state_file: Path | None = None,
    force: bool = False,
    mark_sent: bool | None = None,
) -> dict[str, Any]:
    """Send the daily summary if due. Safe no-op when password missing or too early.

    ``force=True`` bypasses the 16:00 IST / once-per-day gate **and** the
    NSE trading-day gate (for local test resends on weekends/holidays). By
    default a forced send does **not** write the dedup marker so the scheduled
    16:00 email can still go out; pass ``mark_sent=True`` to record it.
    Scheduled sends always mark when SMTP succeeds (``mark_sent`` defaults to
    ``not force``).

    Returns a small status dict for logging / tests.
    """
    cfg = daily_summary_config(config)
    if not cfg["enabled"] and not force:
        return {"sent": False, "reason": "disabled"}

    settings = gmail_settings()
    notifier = gmail or GmailSMTPNotifier(settings=settings)
    if not notifier.enabled:
        return {
            "sent": False,
            "reason": "gmail_not_configured",
            "hint": "Set GMAIL_APP_PASSWORD in local .env (Windows). Cloud cannot send without it.",
        }

    current = now or now_ist()
    if current.tzinfo is None:
        current = current.replace(tzinfo=ist())
    else:
        current = current.astimezone(ist())
    today = current.date()
    path = state_file or state_path()
    last = load_last_sent_day(path)
    should_mark = (not force) if mark_sent is None else bool(mark_sent)

    # Sat/Sun + NSE holiday stub — no scheduled daily email (force still allowed)
    if not force and not is_nse_trading_day(today):
        return {
            "sent": False,
            "reason": "non_trading_day",
            "day_ist": today.isoformat(),
            "last_sent_ist_day": last.isoformat() if last else None,
            "now_ist": current.isoformat(),
        }

    if not force and not should_send_daily_summary(
        current,
        hour_ist=cfg["hour_ist"],
        minute_ist=cfg["minute_ist"],
        last_sent=last,
    ):
        return {
            "sent": False,
            "reason": "not_due",
            "last_sent_ist_day": last.isoformat() if last else None,
            "now_ist": current.isoformat(),
        }

    mode = "dry_run"
    if config:
        mode = str((config.get("trading") or {}).get("mode") or "dry_run")
        if mode == "live":
            mode = "dry_run"

    positions: list[dict[str, Any]] = []
    recent_fills: list[dict[str, Any]] = []
    day_realized = 0.0
    store = paper_store
    if store is None:
        try:
            from options_portal_persistence.paper_trades import PaperTradeStore

            store = PaperTradeStore()
        except Exception:  # noqa: BLE001
            logger.exception("daily summary: paper store unavailable")
            store = None

    if store is not None and getattr(store, "available", False):
        snap = store.portfolio_snapshot(mode=mode)  # type: ignore[arg-type]
        positions = list(snap.get("positions") or [])
        day_realized = float(snap.get("day_realized_pnl") or 0.0)
        # Recent history (not day-filtered) so overnight entries pair with today's exits
        recent_fills = list(
            store.list_fills(mode=mode, limit=200, offset=0)  # type: ignore[arg-type]
        )

    ok = notifier.send_daily_summary(
        day_ist=today.isoformat(),
        day_realized=day_realized,
        positions=positions,
        fills=recent_fills,
        mode=mode,
    )
    fills_today_count = len(fills_for_ist_day(recent_fills, today))

    # Adaptive |hybrid| feedback from past-week WR (persist ±0.05 delta).
    adaptive_status: dict[str, Any] = {"updated": False, "reason": "skipped"}
    try:
        from options_portal_domain.adaptive_hybrid import run_adaptive_feedback
        from options_portal_domain.high_conviction import (
            rolling_equity_win_rate,
            skip_entry_log,
        )

        adaptive_status = run_adaptive_feedback(
            recent_fills, cfg=config, now=current
        )
        wr, wins, n = rolling_equity_win_rate(recent_fills)
        skip_snap = skip_entry_log().snapshot()
        logger.info(
            "Daily summary extras: rolling_wr=%s skip_log=%s adaptive=%s",
            f"{wr:.0%}({wins}/{n})" if wr is not None else "n/a",
            skip_snap,
            adaptive_status,
        )
    except Exception:  # noqa: BLE001
        logger.exception("daily summary: adaptive / skip-log extras failed")

    if ok and should_mark:
        save_last_sent_day(today, path)
    return {
        "sent": ok,
        "day_ist": today.isoformat(),
        "fills_today": fills_today_count,
        "open_positions": len(positions),
        "day_realized": day_realized,
        "marked_sent": bool(ok and should_mark),
        "adaptive": adaptive_status,
        "reason": "ok" if ok else "smtp_failed",
    }
