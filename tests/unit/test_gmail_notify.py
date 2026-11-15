"""Gmail SMTP notify — mocked SMTP (no network, no secrets)."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from options_portal_domain.tz import ist
from options_portal_notify.daily_summary import (
    fills_for_ist_day,
    load_last_sent_day,
    maybe_send_daily_summary,
    save_last_sent_day,
    should_send_daily_summary,
)
from options_portal_notify.factory import (
    build_gmail_notifier,
    build_notifier,
    gmail_from_notifier,
)
from options_portal_notify.gmail import (
    DEFAULT_NOTIFY_EMAIL_TO,
    GmailSMTPNotifier,
    format_daily_summary_email,
    format_fill_email,
    gmail_settings,
    send_gmail_message,
    soften_reason,
)


@pytest.fixture()
def clear_gmail_env(monkeypatch: pytest.MonkeyPatch):
    for key in ("GMAIL_APP_PASSWORD", "GMAIL_USER", "NOTIFY_EMAIL_TO"):
        monkeypatch.delenv(key, raising=False)


def test_gmail_settings_defaults(clear_gmail_env, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")
    cfg = gmail_settings()
    assert cfg.to_addr == DEFAULT_NOTIFY_EMAIL_TO
    assert cfg.user == DEFAULT_NOTIFY_EMAIL_TO
    assert cfg.configured is True
    assert "abcd" in cfg.password


def test_gmail_settings_disabled_without_password(clear_gmail_env):
    cfg = gmail_settings(enabled=True)
    assert cfg.configured is False


def test_soften_reason_truncates():
    short = soften_reason("ok")
    assert short == "ok"
    long_reason = "x" * 100
    soft = soften_reason(long_reason, max_len=20)
    assert soft.endswith("…")
    assert len(soft) == 20
    assert soften_reason("") == "—"
    assert soften_reason(None) == "—"


def test_format_fill_email():
    subject, body, html_body = format_fill_email(
        symbol="RELIANCE",
        side="BUY",
        qty=1,
        price=2500.5,
        reason="hybrid bullish",
        ts_ist_display="2026-11-15 10:30 IST",
        action_kind="entry",
    )
    assert "[dry-run]" in subject
    assert "RELIANCE" in subject
    assert "BUY" in body
    assert "2500.50" in body
    assert "dry_run" in body
    assert "<table" in html_body
    assert "RELIANCE" in html_body


def test_format_daily_summary_email():
    long_reason = (
        "hybrid bullish: RSI recovery + positive news cluster on earnings "
        "beat and guidance raise with elevated volume confirmation"
    )
    subject, body, html_body = format_daily_summary_email(
        day_ist="2026-11-15",
        day_realized=12.5,
        positions=[{"symbol": "TCS", "side": "long", "qty": 1, "avg_price": 3900}],
        fills_today=[
            {
                "symbol": "TCS",
                "side": "BUY",
                "qty": 1,
                "price": 3900,
                "ts_ist_display": "2026-11-15 11:00 IST",
                "reason": long_reason,
                "realized_pnl": None,
            }
        ],
    )
    assert "2026-11-15" in subject
    assert "TCS" in body
    assert "12.50" in body
    assert "Time (IST)" in body
    assert "Reason" in body
    assert "…" in body  # truncated reason in plain table
    assert long_reason in body  # full reason appendix
    assert "<table" in html_body
    assert "Today's fills" in html_body or "Today&#x27;s fills" in html_body
    assert "Open positions" in html_body
    assert "title=" in html_body  # hover full reason


def test_send_gmail_message_mocked(clear_gmail_env, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "test-app-password")
    monkeypatch.setenv("GMAIL_USER", "venkatareddy.dhatla@gmail.com")
    monkeypatch.setenv("NOTIFY_EMAIL_TO", "venkatareddy.dhatla@gmail.com")

    smtp_instance = MagicMock()
    smtp_factory = MagicMock(return_value=smtp_instance)
    smtp_instance.__enter__ = MagicMock(return_value=smtp_instance)
    smtp_instance.__exit__ = MagicMock(return_value=False)

    ok = send_gmail_message(
        subject="[dry-run] Test",
        body="hello",
        html_body="<p>hello</p>",
        smtp_factory=smtp_factory,
    )
    assert ok is True
    smtp_factory.assert_called_once()
    smtp_instance.starttls.assert_called_once()
    smtp_instance.login.assert_called_once_with(
        "venkatareddy.dhatla@gmail.com", "test-app-password"
    )
    smtp_instance.send_message.assert_called_once()
    sent_msg = smtp_instance.send_message.call_args.args[0]
    assert sent_msg.get_content_type() == "multipart/alternative"


def test_send_gmail_skipped_without_password(clear_gmail_env):
    smtp_factory = MagicMock()
    ok = send_gmail_message(subject="x", body="y", smtp_factory=smtp_factory)
    assert ok is False
    smtp_factory.assert_not_called()


@pytest.mark.asyncio
async def test_gmail_notifier_send_noop_for_hold(
    clear_gmail_env, monkeypatch: pytest.MonkeyPatch
):
    """HOLD / scan ticks must never email — CompositeNotifier.send is a no-op."""
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "pw")
    smtp_instance = MagicMock()
    smtp_factory = MagicMock(return_value=smtp_instance)
    smtp_instance.__enter__ = MagicMock(return_value=smtp_instance)
    smtp_instance.__exit__ = MagicMock(return_value=False)

    n = GmailSMTPNotifier(enabled=True, smtp_factory=smtp_factory)
    assert n.enabled
    await n.send("RELIANCE HOLD", "hybrid=0.1 (neutral)", {"symbol": "RELIANCE"})
    smtp_factory.assert_not_called()
    smtp_instance.send_message.assert_not_called()


def test_build_notifier_excludes_gmail(clear_gmail_env, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "pw")
    cfg = {
        "notify": {
            "channels": [
                {"type": "console", "enabled": True},
                {"type": "gmail", "enabled": True},
            ]
        }
    }
    composite = build_notifier(cfg)
    assert gmail_from_notifier(composite) is None
    gmail = build_gmail_notifier(cfg)
    assert gmail is not None
    assert gmail.enabled


def test_should_send_daily_summary_window():
    # Monday 2026-11-16 (trading day)
    before = datetime(2026, 11, 16, 15, 59, tzinfo=ist())
    after = datetime(2026, 11, 16, 16, 0, tzinfo=ist())
    assert should_send_daily_summary(before, last_sent=None) is False
    assert should_send_daily_summary(after, last_sent=None) is True
    assert (
        should_send_daily_summary(after, last_sent=date(2026, 11, 16)) is False
    )


def test_fills_for_ist_day():
    fills = [
        {"ts_ist_display": "2026-11-16 10:00 IST", "symbol": "A"},
        {"ts_ist_display": "2026-11-14 10:00 IST", "symbol": "B"},
    ]
    today = fills_for_ist_day(fills, date(2026, 11, 16))
    assert len(today) == 1
    assert today[0]["symbol"] == "A"


def test_daily_summary_state_roundtrip(tmp_path: Path):
    path = tmp_path / "state.json"
    assert load_last_sent_day(path) is None
    save_last_sent_day(date(2026, 11, 16), path)
    assert load_last_sent_day(path) == date(2026, 11, 16)


def test_maybe_send_daily_summary_mocked(
    clear_gmail_env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "pw")
    monkeypatch.setenv("NOTIFY_EMAIL_TO", "venkatareddy.dhatla@gmail.com")

    smtp_instance = MagicMock()
    smtp_factory = MagicMock(return_value=smtp_instance)
    smtp_instance.__enter__ = MagicMock(return_value=smtp_instance)
    smtp_instance.__exit__ = MagicMock(return_value=False)

    class FakeStore:
        available = True

        def portfolio_snapshot(self, *, mode="dry_run"):
            return {
                "positions": [
                    {"symbol": "INFY", "side": "long", "qty": 1, "avg_price": 1500.0}
                ],
                "day_realized_pnl": 5.0,
            }

        def list_fills(self, *, mode="dry_run", limit=200, offset=0):
            return [
                {
                    "symbol": "INFY",
                    "side": "BUY",
                    "qty": 1,
                    "price": 1500.0,
                    "ts_ist_display": "2026-11-16 12:00 IST",
                    "reason": "entry",
                    "realized_pnl": None,
                }
            ]

    gmail = GmailSMTPNotifier(enabled=True, smtp_factory=smtp_factory)
    # Monday 2026-11-16 — NSE trading day
    now = datetime(2026, 11, 16, 16, 5, tzinfo=ist())
    state = tmp_path / "notify_state.json"
    status = maybe_send_daily_summary(
        config={"notify": {"daily_summary": {"enabled": True, "hour_ist": 16}}},
        gmail=gmail,
        paper_store=FakeStore(),
        now=now,
        state_file=state,
    )
    assert status["sent"] is True
    assert status["fills_today"] == 1
    assert load_last_sent_day(state) == date(2026, 11, 16)

    # Second call same day → not_due
    status2 = maybe_send_daily_summary(
        config={"notify": {"daily_summary": {"enabled": True, "hour_ist": 16}}},
        gmail=gmail,
        paper_store=FakeStore(),
        now=now,
        state_file=state,
    )
    assert status2["sent"] is False
    assert status2["reason"] == "not_due"


def test_daily_summary_skips_sunday_without_force(
    clear_gmail_env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "pw")
    smtp_instance = MagicMock()
    smtp_factory = MagicMock(return_value=smtp_instance)
    smtp_instance.__enter__ = MagicMock(return_value=smtp_instance)
    smtp_instance.__exit__ = MagicMock(return_value=False)

    class EmptyStore:
        available = True

        def portfolio_snapshot(self, *, mode="dry_run"):
            return {"positions": [], "day_realized_pnl": 0.0}

        def list_fills(self, *, mode="dry_run", limit=200, offset=0):
            return []

    gmail = GmailSMTPNotifier(enabled=True, smtp_factory=smtp_factory)
    # Sunday 2026-11-15 — after 16:00 but non-trading
    now = datetime(2026, 11, 15, 16, 5, tzinfo=ist())
    state = tmp_path / "notify_state.json"
    status = maybe_send_daily_summary(
        config={"notify": {"daily_summary": {"enabled": True, "hour_ist": 16}}},
        gmail=gmail,
        paper_store=EmptyStore(),
        now=now,
        state_file=state,
    )
    assert status["sent"] is False
    assert status["reason"] == "non_trading_day"
    smtp_factory.assert_not_called()
    assert load_last_sent_day(state) is None


def test_daily_summary_skips_holiday_stub(
    clear_gmail_env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "pw")
    smtp_instance = MagicMock()
    smtp_factory = MagicMock(return_value=smtp_instance)
    smtp_instance.__enter__ = MagicMock(return_value=smtp_instance)
    smtp_instance.__exit__ = MagicMock(return_value=False)

    class EmptyStore:
        available = True

        def portfolio_snapshot(self, *, mode="dry_run"):
            return {"positions": [], "day_realized_pnl": 0.0}

        def list_fills(self, *, mode="dry_run", limit=200, offset=0):
            return []

    gmail = GmailSMTPNotifier(enabled=True, smtp_factory=smtp_factory)
    # Republic Day 2026 (Mon) in NSE_HOLIDAYS_2026 stub
    now = datetime(2026, 1, 26, 16, 5, tzinfo=ist())
    status = maybe_send_daily_summary(
        config={"notify": {"daily_summary": {"enabled": True, "hour_ist": 16}}},
        gmail=gmail,
        paper_store=EmptyStore(),
        now=now,
        state_file=tmp_path / "notify_state.json",
    )
    assert status["sent"] is False
    assert status["reason"] == "non_trading_day"
    smtp_factory.assert_not_called()


def test_force_send_on_sunday_still_works(
    clear_gmail_env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "pw")
    smtp_instance = MagicMock()
    smtp_factory = MagicMock(return_value=smtp_instance)
    smtp_instance.__enter__ = MagicMock(return_value=smtp_instance)
    smtp_instance.__exit__ = MagicMock(return_value=False)

    class EmptyStore:
        available = True

        def portfolio_snapshot(self, *, mode="dry_run"):
            return {"positions": [], "day_realized_pnl": 0.0}

        def list_fills(self, *, mode="dry_run", limit=200, offset=0):
            return []

    gmail = GmailSMTPNotifier(enabled=True, smtp_factory=smtp_factory)
    now = datetime(2026, 11, 15, 10, 0, tzinfo=ist())  # Sunday
    state = tmp_path / "notify_state.json"
    status = maybe_send_daily_summary(
        config={"notify": {"daily_summary": {"enabled": True, "hour_ist": 16}}},
        gmail=gmail,
        paper_store=EmptyStore(),
        now=now,
        state_file=state,
        force=True,
        mark_sent=False,
    )
    assert status["sent"] is True
    assert status["marked_sent"] is False
    smtp_instance.send_message.assert_called_once()


def test_force_send_without_mark_preserves_schedule(
    clear_gmail_env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "pw")
    smtp_instance = MagicMock()
    smtp_factory = MagicMock(return_value=smtp_instance)
    smtp_instance.__enter__ = MagicMock(return_value=smtp_instance)
    smtp_instance.__exit__ = MagicMock(return_value=False)

    class EmptyStore:
        available = True

        def portfolio_snapshot(self, *, mode="dry_run"):
            return {"positions": [], "day_realized_pnl": 0.0}

        def list_fills(self, *, mode="dry_run", limit=200, offset=0):
            return []

    gmail = GmailSMTPNotifier(enabled=True, smtp_factory=smtp_factory)
    # Monday before 16:00 — only force can send
    now = datetime(2026, 11, 16, 10, 0, tzinfo=ist())
    state = tmp_path / "notify_state.json"
    status = maybe_send_daily_summary(
        config={"notify": {"daily_summary": {"enabled": True, "hour_ist": 16}}},
        gmail=gmail,
        paper_store=EmptyStore(),
        now=now,
        state_file=state,
        force=True,
        mark_sent=False,
    )
    assert status["sent"] is True
    assert status["marked_sent"] is False
    assert load_last_sent_day(state) is None

    # Scheduled path still eligible later today
    later = datetime(2026, 11, 16, 16, 5, tzinfo=ist())
    assert should_send_daily_summary(later, last_sent=load_last_sent_day(state))


def test_send_fill_helper(clear_gmail_env, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "pw")
    smtp_instance = MagicMock()
    smtp_factory = MagicMock(return_value=smtp_instance)
    smtp_instance.__enter__ = MagicMock(return_value=smtp_instance)
    smtp_instance.__exit__ = MagicMock(return_value=False)

    n = GmailSMTPNotifier(enabled=True, smtp_factory=smtp_factory)
    ok = n.send_fill(
        {
            "symbol": "SBIN",
            "side": "SELL",
            "qty": 2,
            "price": 800.0,
            "reason": "exit",
            "action_kind": "exit",
            "ts_ist_display": "2026-11-15 14:00 IST",
        }
    )
    assert ok is True
    smtp_instance.send_message.assert_called_once()
