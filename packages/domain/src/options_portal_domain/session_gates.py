"""Soft EOD flatten + optional risk exits + morning/ATR helpers (IST).

Product rules (equity dry-run) — Pine entry + Action+Trend open-book exits:

- **New entries (open/add / flip_open)** only on NSE trading days inside the
  cash entry window [entries_open_ist, no_new_entries_after) IST (default
  **09:30–15:15**). First 15m candle (09:15–09:30) must complete before any
  new entry. Use ``is_trading_session`` / ``SoftEodPolicy.allow_new_entries`` —
  nights, pre-09:30, and post-cutoff must not persist entry fills or send
  entry Gmail. Primary entry: **Action+Trend aligned** (BUY+BULLISH /
  SELL+BEARISH). Risk exits / soft flatten still run through soft_flatten_until.
- **Index underlyings** (NIFTY, BANKNIFTY, … / NSE_INDEX|BSE_INDEX) never use
  stock hybrid entry; open index paper is soft-closed with ``index_not_equity``.
- **Fixed ±2% open-book exits** (`trading.risk.fixed_pct_exit`) are **OFF** by
  default (``enabled: false``). Code path stays for rollback only —
  ``pct_profit_2`` / ``pct_stop_2`` must not fire on the dry-run tip.
  Same-tick priority when re-enabled: stop > profit > profit_candle_protect >
  (disabled ATR/morning) > aligned_action_trend > soft_flatten.
- **Profit candle protect** (`trading.risk.profit_candle_protect`, open book):
  structure exit (not a % cousin) when an open stock is **in profit**, latest
  15m vs prior:
  - Long: open < mid(prior) **and** close < prior.low → ``profit_candle_protect``
  - Short: open > mid(prior) **and** close > prior.high → ``profit_candle_protect``
  where ``mid = (prior.high + prior.low) / 2``. Optional secondary; primary
  strategy exits are aligned opposite / NEUTRAL / 15:15 flatten.
- **Aligned Action+Trend open-book** (`trading.risk.aligned_action_trend_exit`):
  - Open **long** + Action=SELL + Trend=BEARISH → close long **and** open short
  - Open **short** + Action=BUY + Trend=BULLISH → cover short **and** open long
  - Trend=NEUTRAL → close open and wait (no reverse)
  - Misaligned Action/Trend → no exit from this path
  - One open trade per stock max (same-side aligned → hold)
  - No cooloff after close
- **Open-book risk exits** (``profit_candle_protect``, plus optional
  ``pct_profit_*`` / ``pct_stop_*`` / ``profit_book`` / ``atr_*`` /
  ``morning_*`` when re-enabled) only on NSE trading days inside
  ``[open_ist, soft_flatten_until_ist]`` IST (default **09:15–15:30**). Use
  ``allow_open_book_risk_exits``. Outside that window the worker may still poll
  for marks / news / TA — **no risk-exit fills**. Cash/risk ``open_ist`` stays
  **09:15** even when entries wait until 09:30. Aligned Action+Trend exits
  follow the Pine signal path (trading day) and do not require the LTP risk gate.
- **ATR trail / morning dynamic TP** remain in code but are **disabled**
  (`atr_trail_scale_out.enabled: false`, `morning_profit_book.enabled: false`).
  Soft EOD flatten stays as safety.
- Soft flatten window [soft_flatten_ist, soft_flatten_until_ist] inclusive by
  HH:MM (default **15:15–15:30 IST**): **flatten ALL** open paper equity
  (overnight hold off by default). Separate from the entry window (entries cut
  at 15:15).
- After soft_flatten_until_ist: refuse new soft_flatten exits (do not invent
  late-afternoon paper fills on a stuck/restarted worker). Positions left open
  by a missed window stay open — this is not a hard force square-off.
- Daily summary (16:00 IST) is also a separate schedule gate.
- Optional light NIFTY/BANKNIFTY gate: block new opposite entries only when
  index hybrid strongly conflicts; skip when noisy/weak.
- Worker may keep scanning overnight for news/TA / mark refresh; orders/fills
  for Action entry and open-book risk exits must not.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Literal, Sequence

from options_portal_domain.equity import (
    EquityActionKind,
    PositionSide,
    profit_book_exit_qty,
    unrealized_pnl_pct,
)
from options_portal_domain.tz import ist, now_ist
from options_portal_domain.types import Candle, SignalAction

IndexBias = Literal["bullish", "bearish", "neutral", "noisy", "unavailable"]

# Worker open-book soft-flatten / morning-TP poll (independent of full-universe scan).
DEFAULT_SOFT_FLATTEN_POLL_SEC = 15
DEFAULT_MORNING_TP_IST = "09:15"
DEFAULT_MORNING_TP_UNTIL_IST = "10:00"
# Dynamic morning thresholds (ATR-aware; % floors when ATR missing).
# Dual-session: hold through noise — book ≥ max(1.0% entry, 1.5×ATR14).
DEFAULT_MORNING_TP_MIN_PCT = 1.0
DEFAULT_MORNING_TP_ATR_MULT = 1.5
DEFAULT_MORNING_STOP_MIN_PCT = 1.0  # stop ≤ −max(1.0% entry, 1.25×ATR14)
DEFAULT_MORNING_STOP_ATR_MULT = 1.25
DEFAULT_MORNING_ATR_PERIOD = 14

# Fixed ±2% profit / stop on open equity (trading.risk.fixed_pct_exit).
DEFAULT_FIXED_PCT_PROFIT = 2.0
DEFAULT_FIXED_PCT_STOP = 2.0

# In-profit structure break on latest 15m vs prior (trading.risk.profit_candle_protect).
PROFIT_CANDLE_PROTECT_REASON = "profit_candle_protect"

# Close open book on opposite fresh Pine BUY/SELL (trading.risk.opposite_pine_exit).
# Legacy close-only path; prefer aligned_action_trend_exit for Venkata rules.
OPPOSITE_PINE_SELL_REASON = "opposite_pine_sell"
OPPOSITE_PINE_BUY_REASON = "opposite_pine_buy"

# Action+Trend aligned open-book exits / flips (trading.risk.aligned_action_trend_exit).
ALIGNED_NEUTRAL_EXIT_REASON = "aligned_neutral_exit"
ALIGNED_OPPOSITE_SELL_REASON = "aligned_opposite_sell"
ALIGNED_OPPOSITE_BUY_REASON = "aligned_opposite_buy"

# Session defaults — entries 09:30–15:15; flatten ALL from 15:15.
DEFAULT_NO_NEW_ENTRIES_AFTER = "15:15"
DEFAULT_SOFT_FLATTEN_IST = "15:15"
DEFAULT_SOFT_FLATTEN_UNTIL_IST = "15:30"
DEFAULT_OVERNIGHT_HOLD_ENABLED = False
DEFAULT_OVERNIGHT_HOLD_MIN_HYBRID = 0.85
DEFAULT_USER_HOURS_NOTE = (
    "09:15–15:30 IST (entries 09:30–15:15; flatten ALL from 15:15; "
    "Action+Trend aligned entries/exits; no ±2%; profit_candle optional; "
    "no overnight hold; no cooloff)"
)

# All-day ATR trail + profit scale-out (trading.risk.atr_trail_scale_out).
# Wider stops + higher profit_book so tiny scalp books stop firing.
DEFAULT_ATR_TRAIL_ATR_PERIOD = 14
DEFAULT_ATR_INITIAL_STOP_ATR_MULT = 2.0
DEFAULT_ATR_INITIAL_STOP_PCT = 1.2  # % of entry when ATR missing
DEFAULT_ATR_TRAIL_ACTIVATE_ATR_MULT = 1.5
DEFAULT_ATR_TRAIL_ACTIVATE_PCT = 0.8
DEFAULT_ATR_TRAIL_ATR_MULT = 1.2  # distance from extreme
DEFAULT_ATR_PROFIT_ATR_MULT = 2.5
DEFAULT_ATR_PROFIT_PCT = 1.2
DEFAULT_ATR_SCALE_OUT_FRACTION = 0.5
# Discretionary / profit exits: profit distance ≥ min_rrr × initial stop distance.
DEFAULT_ATR_MIN_RRR = 1.2


def parse_hhmm(value: str) -> time:
    hour, minute = str(value).strip().split(":")[:2]
    return time(int(hour), int(minute))


def _ist_clock(now: datetime | None = None) -> datetime:
    dt = now or now_ist()
    if dt.tzinfo is None:
        # Treat naive as already-IST wall clock for tests
        return dt
    return dt.astimezone(ist())


def _hhmm_tuple(value: time | str) -> tuple[int, int]:
    t = parse_hhmm(value) if isinstance(value, str) else value
    return (int(t.hour), int(t.minute))


def at_or_after_ist(hhmm: str, now: datetime | None = None) -> bool:
    clock = _ist_clock(now)
    return _hhmm_tuple(clock.time()) >= _hhmm_tuple(hhmm)


def hhmm_in_inclusive_window(
    start_hhmm: str,
    until_hhmm: str,
    now: datetime | None = None,
) -> bool:
    """True when IST clock HH:MM is in [start, until] inclusive."""
    clock = _hhmm_tuple(_ist_clock(now).time())
    start = _hhmm_tuple(start_hhmm)
    until = _hhmm_tuple(until_hhmm)
    if start <= until:
        return start <= clock <= until
    # Rare wrap across midnight — keep behavior defined.
    return clock >= start or clock <= until


# First new entries only after the opening 15m bar completes.
DEFAULT_ENTRIES_OPEN_IST = "09:30"


@dataclass(frozen=True)
class SoftEodPolicy:
    """Session soft-EOD knobs from trading.session (+ equity overnight threshold)."""

    open_ist: str = "09:15"  # cash / risk-exit window start (NSE open)
    close_ist: str = "15:30"
    # New entries wait for first 15m candle; window ends at no_new_entries_after.
    entries_open_ist: str = DEFAULT_ENTRIES_OPEN_IST
    no_new_entries_after: str = DEFAULT_NO_NEW_ENTRIES_AFTER
    soft_flatten_ist: str = DEFAULT_SOFT_FLATTEN_IST
    # Last HH:MM IST minute where soft_flatten exits may still fire (inclusive).
    soft_flatten_until_ist: str = DEFAULT_SOFT_FLATTEN_UNTIL_IST
    overnight_hold_enabled: bool = DEFAULT_OVERNIGHT_HOLD_ENABLED
    overnight_hold_min_hybrid_abs: float = DEFAULT_OVERNIGHT_HOLD_MIN_HYBRID
    soft_flatten_poll_interval_sec: int = DEFAULT_SOFT_FLATTEN_POLL_SEC
    morning_tp_ist: str = DEFAULT_MORNING_TP_IST
    morning_tp_until_ist: str = DEFAULT_MORNING_TP_UNTIL_IST
    # Documented user monitoring window (flatten ALL from soft_flatten_ist)
    user_hours_note: str = DEFAULT_USER_HOURS_NOTE

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> SoftEodPolicy:
        trading = config.get("trading") or {}
        session = dict(trading.get("session") or {})
        equity = dict(trading.get("equity") or {})
        # Prefer session key; allow equity.* override for overnight threshold
        overnight = session.get(
            "overnight_hold_min_hybrid_abs",
            equity.get(
                "overnight_hold_min_hybrid_abs",
                equity.get(
                    "overnight_hold_min_abs_hybrid", DEFAULT_OVERNIGHT_HOLD_MIN_HYBRID
                ),
            ),
        )
        overnight_enabled = session.get(
            "overnight_hold_enabled",
            equity.get("overnight_hold_enabled", DEFAULT_OVERNIGHT_HOLD_ENABLED),
        )
        poll = session.get(
            "soft_flatten_poll_interval_sec", DEFAULT_SOFT_FLATTEN_POLL_SEC
        )
        env_poll = os.environ.get("SOFT_FLATTEN_POLL_SEC")
        if env_poll is not None and str(env_poll).strip() != "":
            poll = env_poll
        morning = dict(session.get("morning_profit_book") or equity.get("morning_profit_book") or {})
        return cls(
            open_ist=str(session.get("open", "09:15")),
            close_ist=str(session.get("close", "15:30")),
            entries_open_ist=str(
                session.get(
                    "entries_open",
                    session.get("entries_open_ist", DEFAULT_ENTRIES_OPEN_IST),
                )
            ),
            no_new_entries_after=str(
                session.get("no_new_entries_after", DEFAULT_NO_NEW_ENTRIES_AFTER)
            ),
            soft_flatten_ist=str(
                session.get("soft_flatten_ist", DEFAULT_SOFT_FLATTEN_IST)
            ),
            soft_flatten_until_ist=str(
                session.get("soft_flatten_until_ist", DEFAULT_SOFT_FLATTEN_UNTIL_IST)
            ),
            overnight_hold_enabled=bool(overnight_enabled),
            overnight_hold_min_hybrid_abs=float(overnight),
            soft_flatten_poll_interval_sec=max(5, int(poll)),
            morning_tp_ist=str(
                morning.get(
                    "start_ist",
                    session.get("morning_tp_ist", DEFAULT_MORNING_TP_IST),
                )
            ),
            morning_tp_until_ist=str(
                morning.get(
                    "until_ist",
                    session.get("morning_tp_until_ist", DEFAULT_MORNING_TP_UNTIL_IST),
                )
            ),
            user_hours_note=str(
                session.get("user_hours_note", DEFAULT_USER_HOURS_NOTE)
            ),
        )

    def in_risk_exit_window(self, now: datetime | None = None) -> bool:
        """True during inclusive open-book risk-exit HH:MM window.

        Default ``[open_ist, soft_flatten_until_ist]`` = **09:15–15:30 IST**.
        Covers ATR stop/trail/profit_book/scale-out and morning TP/stop.
        Soft flatten uses ``[soft_flatten_ist, soft_flatten_until_ist]`` (15:15–15:30).
        """
        return hhmm_in_inclusive_window(
            self.open_ist, self.soft_flatten_until_ist, now
        )

    def in_morning_profit_window(self, now: datetime | None = None) -> bool:
        """True during inclusive morning profit-book HH:MM window."""
        return hhmm_in_inclusive_window(
            self.morning_tp_ist, self.morning_tp_until_ist, now
        )

    def past_no_new_entries(self, now: datetime | None = None) -> bool:
        return at_or_after_ist(self.no_new_entries_after, now)

    def before_session_open(self, now: datetime | None = None) -> bool:
        """True when IST clock HH:MM is strictly before cash open_ist (night/pre-open)."""
        clock = _hhmm_tuple(_ist_clock(now).time())
        return clock < _hhmm_tuple(self.open_ist)

    def before_entries_open(self, now: datetime | None = None) -> bool:
        """True when IST clock HH:MM is strictly before entries_open_ist.

        Default entries_open_ist **09:30** — first 15m bar (09:15–09:30) must
        complete before new entries. Cash/risk still use ``open_ist`` (09:15).
        """
        clock = _hhmm_tuple(_ist_clock(now).time())
        return clock < _hhmm_tuple(self.entries_open_ist)

    def past_session_close(self, now: datetime | None = None) -> bool:
        """True when IST clock HH:MM is at or after close_ist (default 15:30)."""
        return at_or_after_ist(self.close_ist, now)

    def in_cash_session(self, now: datetime | None = None) -> bool:
        """True when IST clock is in [open_ist, close_ist) cash hours (default 09:15–15:30).

        Distinct from ``allow_new_entries`` (starts at entries_open_ist / 09:30,
        cuts off at no_new_entries_after / 15:15). Use for market OPEN/CLOSE UI
        status, not entry gating.
        """
        return not self.before_session_open(now) and not self.past_session_close(now)

    def allow_new_entries(self, now: datetime | None = None) -> bool:
        """True when IST clock is in [entries_open_ist, no_new_entries_after).

        Clock-only — callers that also need an NSE trading day should use
        ``is_trading_session``. Default entry window **09:30–15:15 IST**
        (wait for first 15m candle); soft flatten / flatten-ALL from 15:15.
        Open-book ±2% exits still use cash ``open_ist`` (09:15).
        """
        return (
            not self.before_entries_open(now)
            and not self.past_no_new_entries(now)
        )

    def past_soft_flatten(self, now: datetime | None = None) -> bool:
        return at_or_after_ist(self.soft_flatten_ist, now)

    def in_soft_flatten_window(self, now: datetime | None = None) -> bool:
        """True during the inclusive soft-flatten HH:MM window."""
        return hhmm_in_inclusive_window(
            self.soft_flatten_ist, self.soft_flatten_until_ist, now
        )

    def past_soft_flatten_until(self, now: datetime | None = None) -> bool:
        """True when IST clock HH:MM is strictly after soft_flatten_until_ist."""
        clock = _hhmm_tuple(_ist_clock(now).time())
        return clock > _hhmm_tuple(self.soft_flatten_until_ist)


def overnight_hold_allowed(
    open_side: PositionSide | None,
    hybrid_score: float,
    *,
    min_abs: float,
    trend_confirmed: bool = True,
) -> bool:
    """True when same-side |hybrid| meets overnight conviction + trend OK.

    ``trend_confirmed`` defaults True for backward-compatible callers; the
    high-conviction worker passes 15m/1h alignment explicitly.
    """
    if open_side is None:
        return False
    if not trend_confirmed:
        return False
    threshold = abs(float(min_abs))
    score = float(hybrid_score)
    if open_side == "long":
        return score >= threshold
    if open_side == "short":
        return score <= -threshold
    return False


def soft_flatten_decision(
    *,
    open_side: PositionSide | None,
    open_qty: int,
    hybrid_score: float,
    policy: SoftEodPolicy,
    now: datetime | None = None,
    trend_confirmed: bool = True,
) -> dict[str, Any]:
    """Evaluate soft EOD for an open paper position.

    Returns keys: active, action ('flatten'|'overnight_hold'|'none'), reason.
    Default policy **flattens ALL** from soft_flatten_ist (overnight hold off).
    When ``overnight_hold_enabled``, overnight hold still requires |hybrid| ≥
    policy floor **and** ``trend_confirmed``.
    """
    if open_side is None or int(open_qty) <= 0:
        return {"active": False, "action": "none", "reason": "no open position"}
    if not policy.past_soft_flatten(now):
        return {
            "active": False,
            "action": "none",
            "reason": f"before soft_flatten_ist={policy.soft_flatten_ist}",
        }
    if policy.past_soft_flatten_until(now):
        return {
            "active": False,
            "action": "none",
            "reason": (
                f"soft_flatten refused: after soft_flatten_until_ist="
                f"{policy.soft_flatten_until_ist} (missed window; leave open)"
            ),
        }
    if policy.overnight_hold_enabled and overnight_hold_allowed(
        open_side,
        hybrid_score,
        min_abs=policy.overnight_hold_min_hybrid_abs,
        trend_confirmed=trend_confirmed,
    ):
        return {
            "active": True,
            "action": "overnight_hold",
            "reason": (
                f"soft_flatten skipped: same-side conviction "
                f"|hybrid|={abs(hybrid_score):.3f} ≥ "
                f"{policy.overnight_hold_min_hybrid_abs} ({open_side}) "
                f"+ trend_confirmed"
            ),
        }
    if not policy.overnight_hold_enabled:
        return {
            "active": True,
            "action": "flatten",
            "reason": (
                f"soft_flatten_ist={policy.soft_flatten_ist}: flatten ALL "
                f"{open_side} (overnight hold disabled)"
            ),
        }
    trend_note = "" if trend_confirmed else "; trend not confirmed"
    return {
        "active": True,
        "action": "flatten",
        "reason": (
            f"soft_flatten_ist={policy.soft_flatten_ist}: close {open_side} "
            f"(hybrid={hybrid_score:.3f} below overnight "
            f"min_abs={policy.overnight_hold_min_hybrid_abs}{trend_note})"
        ),
    }


@dataclass(frozen=True)
class MorningProfitBookPolicy:
    """First-hour dynamic profit book / stop for open equity (stocks only).

    Window default **09:15–10:00 IST**. Thresholds (per-share vs entry):

    - **TP** ``morning_profit_book``: uPnL ≥ max(tp_min_pct% of entry,
      tp_atr_mult × ATR14)
    - **Stop** ``morning_stop``: uPnL ≤ −max(stop_min_pct% of entry,
      stop_atr_mult × ATR14)
    - If ATR14 unavailable → percent floors only.
    """

    enabled: bool = True
    start_ist: str = DEFAULT_MORNING_TP_IST
    until_ist: str = DEFAULT_MORNING_TP_UNTIL_IST
    tp_min_pct: float = DEFAULT_MORNING_TP_MIN_PCT
    tp_atr_mult: float = DEFAULT_MORNING_TP_ATR_MULT
    stop_min_pct: float = DEFAULT_MORNING_STOP_MIN_PCT
    stop_atr_mult: float = DEFAULT_MORNING_STOP_ATR_MULT
    atr_period: int = DEFAULT_MORNING_ATR_PERIOD
    # 1.0 = full exit (qty is 1 today); set 0.5 later for 50% scale-out.
    exit_fraction: float = 1.0

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> MorningProfitBookPolicy:
        trading = config.get("trading") or {}
        session = dict(trading.get("session") or {})
        equity = dict(trading.get("equity") or {})
        raw = dict(
            session.get("morning_profit_book")
            or equity.get("morning_profit_book")
            or {}
        )
        soft = SoftEodPolicy.from_config(config)
        # Legacy key min_unrealized_pct → tp_min_pct if present.
        tp_pct = raw.get("tp_min_pct", raw.get("min_unrealized_pct", DEFAULT_MORNING_TP_MIN_PCT))
        return cls(
            enabled=bool(raw.get("enabled", True)),
            start_ist=str(raw.get("start_ist", soft.morning_tp_ist)),
            until_ist=str(raw.get("until_ist", soft.morning_tp_until_ist)),
            tp_min_pct=float(tp_pct),
            tp_atr_mult=float(raw.get("tp_atr_mult", DEFAULT_MORNING_TP_ATR_MULT)),
            stop_min_pct=float(
                raw.get("stop_min_pct", DEFAULT_MORNING_STOP_MIN_PCT)
            ),
            stop_atr_mult=float(
                raw.get("stop_atr_mult", DEFAULT_MORNING_STOP_ATR_MULT)
            ),
            atr_period=int(raw.get("atr_period", DEFAULT_MORNING_ATR_PERIOD)),
            exit_fraction=float(raw.get("exit_fraction", 1.0)),
        )

    def in_window(self, now: datetime | None = None) -> bool:
        return hhmm_in_inclusive_window(self.start_ist, self.until_ist, now)

    def thresholds_rupees(
        self, entry_avg: float, atr14: float | None
    ) -> dict[str, Any]:
        """Per-share TP/stop distance in ₹ (vs entry).

        ATR path: max(% floor × entry, atr_mult × ATR).
        No ATR: % floor × entry only.
        """
        entry = float(entry_avg)
        tp_pct_leg = abs(float(self.tp_min_pct)) / 100.0 * entry
        stop_pct_leg = abs(float(self.stop_min_pct)) / 100.0 * entry
        atr_val = float(atr14) if atr14 is not None and float(atr14) > 0 else None
        if atr_val is not None:
            tp = max(tp_pct_leg, float(self.tp_atr_mult) * atr_val)
            stop = max(stop_pct_leg, float(self.stop_atr_mult) * atr_val)
            mode = "atr"
        else:
            tp = tp_pct_leg
            stop = stop_pct_leg
            mode = "pct_only"
        return {
            "tp_rupees": tp,
            "stop_rupees": stop,
            "tp_pct_equiv": (tp / entry * 100.0) if entry > 0 else 0.0,
            "stop_pct_equiv": (stop / entry * 100.0) if entry > 0 else 0.0,
            "mode": mode,
            "atr": atr_val,
        }


def morning_profit_book_decision(
    *,
    open_side: PositionSide | None,
    open_qty: int,
    entry_avg: float | None,
    mark: float | None,
    policy: MorningProfitBookPolicy,
    now: datetime | None = None,
    is_index: bool = False,
    atr14: float | None = None,
    session_policy: SoftEodPolicy | None = None,
    trading_day: bool | None = None,
) -> dict[str, Any]:
    """Evaluate morning dynamic TP/stop for an open equity position.

    Returns keys: active, action ('profit_book'|'stop'|'none'), reason,
    exit_qty, unrealized_pct, unrealized_rupees, thresholds (when computable).

    When ``now`` / ``trading_day`` are supplied, also requires the cash
    risk-exit window (default 09:15–15:40 IST trading days) so overnight
    polls cannot book morning TP/stop fills.
    """
    empty = {
        "exit_qty": 0,
        "unrealized_pct": None,
        "unrealized_rupees": None,
        "thresholds": None,
    }
    if is_index:
        return {
            "active": False,
            "action": "none",
            "reason": "index_not_equity — morning TP/stop stocks only",
            **empty,
        }
    if not policy.enabled:
        return {
            "active": False,
            "action": "none",
            "reason": "morning_profit_book disabled",
            **empty,
        }
    if open_side is None or int(open_qty) <= 0:
        return {
            "active": False,
            "action": "none",
            "reason": "no open position",
            **empty,
        }
    # Cash risk-exit session (when caller supplies clock/day context).
    if now is not None or trading_day is not None:
        if not allow_open_book_risk_exits(
            now, policy=session_policy, trading_day=trading_day
        ):
            soft = session_policy or SoftEodPolicy()
            return {
                "active": False,
                "action": "none",
                "reason": (
                    f"outside_risk_exit_window: "
                    f"{soft.open_ist}-{soft.soft_flatten_until_ist} IST "
                    f"(morning TP/stop refused; marks may still refresh)"
                ),
                **empty,
            }
    if not policy.in_window(now):
        return {
            "active": False,
            "action": "none",
            "reason": (
                f"outside morning_profit_book window "
                f"{policy.start_ist}-{policy.until_ist} IST"
            ),
            **empty,
        }
    if entry_avg is None or float(entry_avg) <= 0 or mark is None or float(mark) <= 0:
        return {
            "active": True,
            "action": "none",
            "reason": "morning window: missing entry_avg/mark for TP/stop check",
            **empty,
        }

    entry = float(entry_avg)
    pct = unrealized_pnl_pct(open_side, entry, float(mark))
    # Per-share signed uPnL in ₹ (long: mark−entry; short: entry−mark).
    upnl_rs = pct / 100.0 * entry
    thr = policy.thresholds_rupees(entry, atr14)
    qty = profit_book_exit_qty(int(open_qty), fraction=policy.exit_fraction)
    mode = thr["mode"]
    atr_note = (
        f"ATR{policy.atr_period}={thr['atr']:.4f}"
        if thr["atr"] is not None
        else f"ATR{policy.atr_period}=unavailable→pct_only"
    )

    if upnl_rs >= float(thr["tp_rupees"]):
        return {
            "active": True,
            "action": "profit_book",
            "reason": (
                f"morning_profit_book: uPnL={pct:.3f}% ({upnl_rs:.4f}/sh) ≥ "
                f"max({policy.tp_min_pct}% entry, {policy.tp_atr_mult}×ATR)="
                f"{thr['tp_pct_equiv']:.3f}% / {thr['tp_rupees']:.4f} "
                f"[{mode}; {atr_note}; {policy.start_ist}-{policy.until_ist} IST]; "
                f"exit_qty={qty}"
            ),
            "exit_qty": qty,
            "unrealized_pct": pct,
            "unrealized_rupees": upnl_rs,
            "thresholds": thr,
        }
    if upnl_rs <= -float(thr["stop_rupees"]):
        return {
            "active": True,
            "action": "stop",
            "reason": (
                f"morning_stop: uPnL={pct:.3f}% ({upnl_rs:.4f}/sh) ≤ "
                f"-max({policy.stop_min_pct}% entry, {policy.stop_atr_mult}×ATR)="
                f"-{thr['stop_pct_equiv']:.3f}% / -{thr['stop_rupees']:.4f} "
                f"[{mode}; {atr_note}; {policy.start_ist}-{policy.until_ist} IST]; "
                f"exit_qty={qty}"
            ),
            "exit_qty": qty,
            "unrealized_pct": pct,
            "unrealized_rupees": upnl_rs,
            "thresholds": thr,
        }
    return {
        "active": True,
        "action": "none",
        "reason": (
            f"morning window: uPnL={pct:.3f}% between "
            f"-{thr['stop_pct_equiv']:.3f}% and +{thr['tp_pct_equiv']:.3f}% "
            f"[{mode}; {atr_note}] — hold"
        ),
        "exit_qty": 0,
        "unrealized_pct": pct,
        "unrealized_rupees": upnl_rs,
        "thresholds": thr,
    }


@dataclass(frozen=True)
class FixedPctExitPolicy:
    """Fixed percent profit / stop for open equity (stocks only).

    Config under ``trading.risk.fixed_pct_exit``. Evaluated on the ~15s
    open-book poll while a stock position is open **inside the cash risk-exit
    window** (default 09:15–15:40 IST trading days). **Not an entry filter** —
    manages existing opens only.

    **Default ``enabled=False``** — dry-run tip must not fire ``pct_profit_2`` /
    ``pct_stop_2``. Thresholds remain +2% / −2% for rollback when re-enabled:

    - Long: mark ≥ entry×(1+profit/100) → ``pct_profit_2``;
      mark ≤ entry×(1−stop/100) → ``pct_stop_2``
    - Short: mark ≤ entry×(1−profit/100) → ``pct_profit_2``;
      mark ≥ entry×(1+stop/100) → ``pct_stop_2``
    """

    enabled: bool = False
    profit_pct: float = DEFAULT_FIXED_PCT_PROFIT
    stop_pct: float = DEFAULT_FIXED_PCT_STOP

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> FixedPctExitPolicy:
        trading = config.get("trading") or {}
        risk = dict(trading.get("risk") or {})
        raw = dict(
            risk.get("fixed_pct_exit")
            or trading.get("fixed_pct_exit")
            or {}
        )
        return cls(
            enabled=bool(raw.get("enabled", False)),
            profit_pct=float(raw.get("profit_pct", DEFAULT_FIXED_PCT_PROFIT)),
            stop_pct=float(raw.get("stop_pct", DEFAULT_FIXED_PCT_STOP)),
        )

    def reason_tags(self) -> tuple[str, str]:
        """Stable reason strings — ``pct_profit_2`` / ``pct_stop_2`` at defaults."""
        profit_tag = f"pct_profit_{_pct_reason_suffix(self.profit_pct)}"
        stop_tag = f"pct_stop_{_pct_reason_suffix(self.stop_pct)}"
        return profit_tag, stop_tag


def _pct_reason_suffix(pct: float) -> str:
    """Format 2.0 → ``2``, 2.5 → ``2_5`` for reason tags."""
    value = abs(float(pct))
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return str(value).replace(".", "_")


def _empty_fixed_pct_result(
    *,
    active: bool,
    action: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "active": active,
        "action": action,
        "reason": reason,
        "exit_qty": 0,
        "unrealized_pct": None,
        "unrealized_rupees": None,
        "thresholds": None,
    }


def fixed_pct_exit_decision(
    *,
    open_side: PositionSide | None,
    open_qty: int,
    entry_avg: float | None,
    mark: float | None,
    policy: FixedPctExitPolicy,
    now: datetime | None = None,
    is_index: bool = False,
    session_policy: SoftEodPolicy | None = None,
    trading_day: bool | None = None,
) -> dict[str, Any]:
    """Evaluate fixed ±pct profit/stop for an open equity position.

    Returns keys: active, action (``pct_profit_2``|``pct_stop_2``|``none``),
    reason, exit_qty, unrealized_pct, unrealized_rupees, thresholds.

    Same-tick priority inside this helper: stop before profit. When ``now`` /
    ``trading_day`` are supplied, refuses fills outside the cash risk-exit
    window. Threshold-only unit tests may omit ``now``.
    """
    if is_index:
        return _empty_fixed_pct_result(
            active=False,
            action="none",
            reason="index_not_equity — fixed pct exits stocks only",
        )
    if not policy.enabled:
        return _empty_fixed_pct_result(
            active=False,
            action="none",
            reason="fixed_pct_exit disabled",
        )
    if open_side is None or int(open_qty) <= 0:
        return _empty_fixed_pct_result(
            active=False,
            action="none",
            reason="no open position",
        )
    if now is not None or trading_day is not None:
        if not allow_open_book_risk_exits(
            now, policy=session_policy, trading_day=trading_day
        ):
            soft = session_policy or SoftEodPolicy()
            return _empty_fixed_pct_result(
                active=False,
                action="none",
                reason=(
                    f"outside_risk_exit_window: "
                    f"{soft.open_ist}-{soft.soft_flatten_until_ist} IST "
                    f"(fixed pct exits refused; marks may still refresh)"
                ),
            )
    if entry_avg is None or float(entry_avg) <= 0 or mark is None or float(mark) <= 0:
        return _empty_fixed_pct_result(
            active=True,
            action="none",
            reason="fixed_pct_exit: missing entry_avg/mark",
        )

    entry = float(entry_avg)
    px = float(mark)
    pct = unrealized_pnl_pct(open_side, entry, px)
    upnl_rs = pct / 100.0 * entry
    profit_thr = abs(float(policy.profit_pct))
    stop_thr = abs(float(policy.stop_pct))
    profit_tag, stop_tag = policy.reason_tags()
    thr = {
        "profit_pct": profit_thr,
        "stop_pct": stop_thr,
        "profit_mark_long": entry * (1.0 + profit_thr / 100.0),
        "stop_mark_long": entry * (1.0 - stop_thr / 100.0),
        "profit_mark_short": entry * (1.0 - profit_thr / 100.0),
        "stop_mark_short": entry * (1.0 + stop_thr / 100.0),
    }
    qty = profit_book_exit_qty(int(open_qty), fraction=1.0)
    side = str(open_side).strip().lower()

    # Stop before profit (mutually exclusive for a single mark, but keep order).
    if pct <= -stop_thr + 1e-12:
        return {
            "active": True,
            "action": stop_tag,
            "reason": (
                f"{stop_tag}: {side} uPnL={pct:.3f}% ≤ −{stop_thr:g}% "
                f"(entry={entry:.4f} mark={px:.4f})"
            ),
            "exit_qty": qty,
            "unrealized_pct": pct,
            "unrealized_rupees": upnl_rs,
            "thresholds": thr,
        }
    if pct >= profit_thr - 1e-12:
        return {
            "active": True,
            "action": profit_tag,
            "reason": (
                f"{profit_tag}: {side} uPnL={pct:.3f}% ≥ +{profit_thr:g}% "
                f"(entry={entry:.4f} mark={px:.4f})"
            ),
            "exit_qty": qty,
            "unrealized_pct": pct,
            "unrealized_rupees": upnl_rs,
            "thresholds": thr,
        }
    return {
        "active": True,
        "action": "none",
        "reason": (
            f"fixed_pct_exit hold: {side} uPnL={pct:.3f}% "
            f"inside ±{profit_thr:g}%/{stop_thr:g}% "
            f"(entry={entry:.4f} mark={px:.4f})"
        ),
        "exit_qty": 0,
        "unrealized_pct": pct,
        "unrealized_rupees": upnl_rs,
        "thresholds": thr,
    }


@dataclass(frozen=True)
class ProfitCandleProtectPolicy:
    """Close an in-profit open when the latest 15m breaks prior structure.

    Config under ``trading.risk.profit_candle_protect``. Stocks only; open book;
    cash risk-exit window. Same-tick priority: after fixed ±2%, before ATR/morning.
    """

    enabled: bool = True

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> ProfitCandleProtectPolicy:
        trading = config.get("trading") or {}
        risk = dict(trading.get("risk") or {})
        raw = dict(
            risk.get("profit_candle_protect")
            or trading.get("profit_candle_protect")
            or {}
        )
        return cls(enabled=bool(raw.get("enabled", True)))


def _empty_profit_candle_result(
    *,
    active: bool,
    action: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "active": active,
        "action": action,
        "reason": reason,
        "exit_qty": 0,
        "unrealized_pct": None,
        "prior_mid": None,
        "prior_low": None,
        "prior_high": None,
        "current_open": None,
        "current_close": None,
    }


def profit_candle_protect_decision(
    *,
    open_side: PositionSide | None,
    open_qty: int,
    entry_avg: float | None,
    mark: float | None,
    candles: Sequence[Candle] | None,
    policy: ProfitCandleProtectPolicy,
    now: datetime | None = None,
    is_index: bool = False,
    session_policy: SoftEodPolicy | None = None,
    trading_day: bool | None = None,
) -> dict[str, Any]:
    """Exit in-profit opens when the latest 15m breaks the prior candle mid/extreme.

    Long (in profit): ``current.open < mid(prior)`` and ``current.close < prior.low``.
    Short (in profit): ``current.open > mid(prior)`` and ``current.close > prior.high``.
    ``mid = (prior.high + prior.low) / 2``. Uses the latest two 15m bars (forming
    last bar allowed so the ~15s open-book poll can exit immediately).
    """
    if is_index:
        return _empty_profit_candle_result(
            active=False,
            action="none",
            reason="index_not_equity — profit candle protect stocks only",
        )
    if not policy.enabled:
        return _empty_profit_candle_result(
            active=False,
            action="none",
            reason="profit_candle_protect disabled",
        )
    if open_side is None or int(open_qty) <= 0:
        return _empty_profit_candle_result(
            active=False,
            action="none",
            reason="no open position",
        )
    if now is not None or trading_day is not None:
        if not allow_open_book_risk_exits(
            now, policy=session_policy, trading_day=trading_day
        ):
            soft = session_policy or SoftEodPolicy()
            return _empty_profit_candle_result(
                active=False,
                action="none",
                reason=(
                    f"outside_risk_exit_window: "
                    f"{soft.open_ist}-{soft.soft_flatten_until_ist} IST "
                    f"(profit candle protect refused)"
                ),
            )
    if entry_avg is None or float(entry_avg) <= 0 or mark is None or float(mark) <= 0:
        return _empty_profit_candle_result(
            active=True,
            action="none",
            reason="profit_candle_protect: missing entry_avg/mark",
        )
    bars = list(candles or ())
    if len(bars) < 2:
        return _empty_profit_candle_result(
            active=True,
            action="none",
            reason="profit_candle_protect: need ≥2 15m candles",
        )

    prior = bars[-2]
    current = bars[-1]
    prior_mid = (float(prior.high) + float(prior.low)) / 2.0
    entry = float(entry_avg)
    px = float(mark)
    pct = unrealized_pnl_pct(open_side, entry, px)
    side = str(open_side).strip().lower()
    cur_open = float(current.open)
    cur_close = float(current.close)
    base = {
        "active": True,
        "exit_qty": 0,
        "unrealized_pct": pct,
        "prior_mid": prior_mid,
        "prior_low": float(prior.low),
        "prior_high": float(prior.high),
        "current_open": cur_open,
        "current_close": cur_close,
    }

    # Protect only while the open is in profit (uPnL > 0).
    if pct <= 0:
        return {
            **base,
            "action": "none",
            "reason": (
                f"profit_candle_protect hold: {side} not in profit "
                f"uPnL={pct:.3f}% (entry={entry:.4f} mark={px:.4f})"
            ),
        }

    fire = False
    if side == "long":
        fire = cur_open < prior_mid and cur_close < float(prior.low)
    elif side == "short":
        fire = cur_open > prior_mid and cur_close > float(prior.high)

    if not fire:
        return {
            **base,
            "action": "none",
            "reason": (
                f"profit_candle_protect hold: {side} structure intact "
                f"open={cur_open:.4f} close={cur_close:.4f} "
                f"mid={prior_mid:.4f} prior_lo={float(prior.low):.4f} "
                f"prior_hi={float(prior.high):.4f}"
            ),
        }

    qty = profit_book_exit_qty(int(open_qty), fraction=1.0)
    return {
        **base,
        "action": PROFIT_CANDLE_PROTECT_REASON,
        "reason": (
            f"{PROFIT_CANDLE_PROTECT_REASON}: {side} in profit uPnL={pct:.3f}% "
            f"open={cur_open:.4f} close={cur_close:.4f} "
            f"mid={prior_mid:.4f} prior_lo={float(prior.low):.4f} "
            f"prior_hi={float(prior.high):.4f}"
        ),
        "exit_qty": qty,
    }


@dataclass(frozen=True)
class OppositePineExitPolicy:
    """Close open equity on a fresh opposite Pine BUY/SELL (close-only).

    Config under ``trading.risk.opposite_pine_exit``. Stocks only; open book.
    Same-tick priority: after (disabled) fixed ±2% / profit_candle /
    (disabled ATR/morning), before soft_flatten. Does **not** open the new direction on the same bar.
    """

    enabled: bool = True

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> OppositePineExitPolicy:
        trading = config.get("trading") or {}
        risk = dict(trading.get("risk") or {})
        raw = dict(
            risk.get("opposite_pine_exit")
            or trading.get("opposite_pine_exit")
            or {}
        )
        return cls(enabled=bool(raw.get("enabled", True)))


def _empty_opposite_pine_result(
    *,
    active: bool,
    action: str,
    reason: str,
    fresh_action: str = "HOLD",
) -> dict[str, Any]:
    return {
        "active": active,
        "action": action,
        "reason": reason,
        "exit_qty": 0,
        "fresh_action": fresh_action,
    }


def opposite_pine_exit_decision(
    *,
    open_side: PositionSide | None,
    open_qty: int,
    pine_action: SignalAction | str | None,
    bar_ts: datetime | str | None,
    policy: OppositePineExitPolicy,
    now: datetime | None = None,
    timeframe_minutes: int = 15,
    is_index: bool = False,
) -> dict[str, Any]:
    """Close open long on fresh Pine SELL; cover open short on fresh Pine BUY.

    Freshness uses the same signal-bar / expire-stale rules as watchlist Action
    (``watchlist_pine_action``): BUY/SELL only while ``bar_ts`` is current or
    still the latest closed 15m bar; otherwise treated as HOLD so a sticky
    Action cannot re-close on every scan hours later.

    Close-only: caller should plan ``SignalAction.EXIT`` (no same-bar flip).
    """
    from options_portal_domain.pine_signals import watchlist_pine_action

    if is_index:
        return _empty_opposite_pine_result(
            active=False,
            action="none",
            reason="index_not_equity — opposite pine exits stocks only",
        )
    if not policy.enabled:
        return _empty_opposite_pine_result(
            active=False,
            action="none",
            reason="opposite_pine_exit disabled",
        )
    if open_side is None or int(open_qty) <= 0:
        return _empty_opposite_pine_result(
            active=False,
            action="none",
            reason="no open position",
        )

    fresh = watchlist_pine_action(
        pine_action,
        bar_ts,
        now=now,
        timeframe_minutes=int(timeframe_minutes) or 15,
    )
    side = str(open_side).strip().lower()
    qty = profit_book_exit_qty(int(open_qty), fraction=1.0)

    if fresh == "HOLD":
        return _empty_opposite_pine_result(
            active=True,
            action="none",
            reason=(
                "opposite_pine_exit hold: Action HOLD or signal bar expired "
                f"(raw={pine_action!s} bar_ts={bar_ts!s})"
            ),
            fresh_action="HOLD",
        )

    if side == "long" and fresh == "SELL":
        return {
            "active": True,
            "action": OPPOSITE_PINE_SELL_REASON,
            "reason": (
                f"{OPPOSITE_PINE_SELL_REASON}: close long on fresh Pine SELL "
                f"(bar_ts={bar_ts})"
            ),
            "exit_qty": qty,
            "fresh_action": "SELL",
        }
    if side == "short" and fresh == "BUY":
        return {
            "active": True,
            "action": OPPOSITE_PINE_BUY_REASON,
            "reason": (
                f"{OPPOSITE_PINE_BUY_REASON}: cover short on fresh Pine BUY "
                f"(bar_ts={bar_ts})"
            ),
            "exit_qty": qty,
            "fresh_action": "BUY",
        }

    return _empty_opposite_pine_result(
        active=True,
        action="none",
        reason=(
            f"opposite_pine_exit hold: open {side} not opposite fresh {fresh}"
        ),
        fresh_action=fresh,
    )


@dataclass(frozen=True)
class AlignedActionTrendExitPolicy:
    """Primary open-book exits from Action+Trend alignment (with flip).

    Config under ``trading.risk.aligned_action_trend_exit``. Stocks only.
    Same-tick priority: after (disabled) fixed ±2% / profit_candle /
    (disabled ATR/morning), before soft_flatten.

    - Opposite aligned pair → close current **and** enter new direction
    - Trend NEUTRAL → close and wait (no reverse)
    """

    enabled: bool = True
    allow_flip: bool = True

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> AlignedActionTrendExitPolicy:
        trading = config.get("trading") or {}
        risk = dict(trading.get("risk") or {})
        raw = dict(
            risk.get("aligned_action_trend_exit")
            or trading.get("aligned_action_trend_exit")
            or {}
        )
        # Default on when key absent (Venkata primary rules).
        return cls(
            enabled=bool(raw.get("enabled", True)),
            allow_flip=bool(raw.get("allow_flip", True)),
        )


def _empty_aligned_result(
    *,
    active: bool,
    action: str,
    reason: str,
    aligned_action: str = "HOLD",
    trend: str = "NEUTRAL",
    flip: bool = False,
    signal_action: str = "HOLD",
) -> dict[str, Any]:
    return {
        "active": active,
        "action": action,
        "reason": reason,
        "exit_qty": 0,
        "aligned_action": aligned_action,
        "trend": trend,
        "flip": flip,
        "signal_action": signal_action,
    }


def aligned_action_trend_decision(
    *,
    open_side: PositionSide | None,
    open_qty: int,
    pine_action: SignalAction | str | None,
    trend: str | None,
    policy: AlignedActionTrendExitPolicy,
    is_index: bool = False,
) -> dict[str, Any]:
    """Open-book decision from Action+Trend alignment.

    Returns ``action``:
      - ``aligned_neutral_exit`` — Trend NEUTRAL → close only
      - ``aligned_opposite_sell`` — long + SELL+BEARISH → close (+ flip if allowed)
      - ``aligned_opposite_buy`` — short + BUY+BULLISH → cover (+ flip if allowed)
      - ``none`` — hold / disabled / no open / same-side / misaligned
    """
    from options_portal_domain.pine_signals import (
        TREND_BEARISH,
        TREND_BULLISH,
        TREND_NEUTRAL,
        aligned_entry_action,
        _normalize_watchlist_action,
    )

    if is_index:
        return _empty_aligned_result(
            active=False,
            action="none",
            reason="index_not_equity — aligned exits stocks only",
        )
    if not policy.enabled:
        return _empty_aligned_result(
            active=False,
            action="none",
            reason="aligned_action_trend_exit disabled",
        )
    if open_side is None or int(open_qty) <= 0:
        return _empty_aligned_result(
            active=False,
            action="none",
            reason="no open position",
        )

    act = _normalize_watchlist_action(pine_action)
    tr = str(trend or TREND_NEUTRAL).strip().upper()
    if tr not in {TREND_BULLISH, TREND_BEARISH, TREND_NEUTRAL}:
        tr = TREND_NEUTRAL
    aligned = aligned_entry_action(act, tr)
    side = str(open_side).strip().lower()
    qty = profit_book_exit_qty(int(open_qty), fraction=1.0)

    if tr == TREND_NEUTRAL:
        return {
            "active": True,
            "action": ALIGNED_NEUTRAL_EXIT_REASON,
            "reason": (
                f"{ALIGNED_NEUTRAL_EXIT_REASON}: close {side} — Trend NEUTRAL "
                f"(Action={act}; wait, no reverse)"
            ),
            "exit_qty": qty,
            "aligned_action": "HOLD",
            "trend": tr,
            "flip": False,
            "signal_action": SignalAction.EXIT.value,
        }

    if aligned == SignalAction.HOLD:
        return _empty_aligned_result(
            active=True,
            action="none",
            reason=(
                f"aligned_action_trend hold: Action={act} Trend={tr} misaligned "
                f"(open {side})"
            ),
            aligned_action="HOLD",
            trend=tr,
        )

    # Same-side aligned — one open max; keep position.
    if side == "long" and aligned == SignalAction.BUY:
        return _empty_aligned_result(
            active=True,
            action="none",
            reason="aligned_action_trend hold: open long already BUY+BULLISH",
            aligned_action="BUY",
            trend=tr,
        )
    if side == "short" and aligned == SignalAction.SELL:
        return _empty_aligned_result(
            active=True,
            action="none",
            reason="aligned_action_trend hold: open short already SELL+BEARISH",
            aligned_action="SELL",
            trend=tr,
        )

    # Opposite aligned pair → close and (optionally) flip.
    if side == "long" and aligned == SignalAction.SELL:
        flip = bool(policy.allow_flip)
        return {
            "active": True,
            "action": ALIGNED_OPPOSITE_SELL_REASON,
            "reason": (
                f"{ALIGNED_OPPOSITE_SELL_REASON}: close long on SELL+BEARISH"
                f"{' + open short' if flip else ' (close-only)'}"
            ),
            "exit_qty": qty,
            "aligned_action": "SELL",
            "trend": tr,
            "flip": flip,
            "signal_action": (
                SignalAction.SELL.value if flip else SignalAction.EXIT.value
            ),
        }
    if side == "short" and aligned == SignalAction.BUY:
        flip = bool(policy.allow_flip)
        return {
            "active": True,
            "action": ALIGNED_OPPOSITE_BUY_REASON,
            "reason": (
                f"{ALIGNED_OPPOSITE_BUY_REASON}: cover short on BUY+BULLISH"
                f"{' + open long' if flip else ' (close-only)'}"
            ),
            "exit_qty": qty,
            "aligned_action": "BUY",
            "trend": tr,
            "flip": flip,
            "signal_action": (
                SignalAction.BUY.value if flip else SignalAction.EXIT.value
            ),
        }

    return _empty_aligned_result(
        active=True,
        action="none",
        reason=f"aligned_action_trend hold: open {side} Action={act} Trend={tr}",
        aligned_action=aligned.value,
        trend=tr,
    )


@dataclass(frozen=True)
class AtrTrailScaleOutPolicy:
    """ATR initial stop + trailing stop + profit scale-out (stocks).

    Config under ``trading.risk.atr_trail_scale_out``. Evaluated on the ~15s
    open-book poll while a stock position is open **inside the cash risk-exit
    window** (default 09:15–15:40 IST trading days; indices excluded).

    Thresholds (per-share vs entry; ATR path uses ATR×mult; missing ATR → %):

    - **Initial stop** ``atr_stop``: uPnL ≤ −stop_atr_mult×ATR (or −stop_pct%)
    - **Trail activate** at uPnL ≥ activate_atr_mult×ATR (or +activate_pct%);
      trail stop = extreme − trail_atr_mult×ATR (long) / extreme + (short);
      extreme updates each poll; trail never loosens; touch → ``atr_trail``
    - **Profit** ≥ profit_atr_mult×ATR (or +profit_pct%): qty>1 → 50%
      ``profit_scale_out``; **qty=1 → full exit ``profit_book``**
    """

    enabled: bool = True
    atr_period: int = DEFAULT_ATR_TRAIL_ATR_PERIOD
    stop_atr_mult: float = DEFAULT_ATR_INITIAL_STOP_ATR_MULT
    stop_pct: float = DEFAULT_ATR_INITIAL_STOP_PCT
    trail_activate_atr_mult: float = DEFAULT_ATR_TRAIL_ACTIVATE_ATR_MULT
    trail_activate_pct: float = DEFAULT_ATR_TRAIL_ACTIVATE_PCT
    trail_atr_mult: float = DEFAULT_ATR_TRAIL_ATR_MULT
    profit_atr_mult: float = DEFAULT_ATR_PROFIT_ATR_MULT
    profit_pct: float = DEFAULT_ATR_PROFIT_PCT
    scale_out_fraction: float = DEFAULT_ATR_SCALE_OUT_FRACTION
    min_rrr: float = DEFAULT_ATR_MIN_RRR

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> AtrTrailScaleOutPolicy:
        trading = config.get("trading") or {}
        risk = dict(trading.get("risk") or {})
        raw = dict(
            risk.get("atr_trail_scale_out")
            or trading.get("atr_trail_scale_out")
            or {}
        )
        hc = dict(risk.get("high_conviction") or {})
        exit_hc = dict(hc.get("exit") or {})
        min_rrr = raw.get("min_rrr", exit_hc.get("min_rrr", DEFAULT_ATR_MIN_RRR))
        return cls(
            enabled=bool(raw.get("enabled", True)),
            atr_period=int(raw.get("atr_period", DEFAULT_ATR_TRAIL_ATR_PERIOD)),
            stop_atr_mult=float(
                raw.get("stop_atr_mult", DEFAULT_ATR_INITIAL_STOP_ATR_MULT)
            ),
            stop_pct=float(raw.get("stop_pct", DEFAULT_ATR_INITIAL_STOP_PCT)),
            trail_activate_atr_mult=float(
                raw.get(
                    "trail_activate_atr_mult", DEFAULT_ATR_TRAIL_ACTIVATE_ATR_MULT
                )
            ),
            trail_activate_pct=float(
                raw.get("trail_activate_pct", DEFAULT_ATR_TRAIL_ACTIVATE_PCT)
            ),
            trail_atr_mult=float(
                raw.get("trail_atr_mult", DEFAULT_ATR_TRAIL_ATR_MULT)
            ),
            profit_atr_mult=float(
                raw.get("profit_atr_mult", DEFAULT_ATR_PROFIT_ATR_MULT)
            ),
            profit_pct=float(raw.get("profit_pct", DEFAULT_ATR_PROFIT_PCT)),
            scale_out_fraction=float(
                raw.get("scale_out_fraction", DEFAULT_ATR_SCALE_OUT_FRACTION)
            ),
            min_rrr=float(min_rrr),
        )

    def distance_rupees(
        self, entry_avg: float, atr14: float | None, *, kind: str
    ) -> tuple[float, str]:
        """Per-share ₹ distance for stop / activate / trail / profit.

        Returns ``(rupees, mode)`` where mode is ``atr`` or ``pct_only``.
        """
        entry = float(entry_avg)
        atr_val = float(atr14) if atr14 is not None and float(atr14) > 0 else None
        if kind == "stop":
            atr_mult, pct = self.stop_atr_mult, self.stop_pct
        elif kind == "activate":
            atr_mult, pct = self.trail_activate_atr_mult, self.trail_activate_pct
        elif kind == "trail":
            atr_mult, pct = self.trail_atr_mult, self.trail_activate_pct
        elif kind == "profit":
            atr_mult, pct = self.profit_atr_mult, self.profit_pct
        else:
            raise ValueError(f"unknown distance kind={kind!r}")
        pct_leg = abs(float(pct)) / 100.0 * entry
        if atr_val is not None:
            return abs(float(atr_mult)) * atr_val, "atr"
        return pct_leg, "pct_only"


def _empty_atr_trail_result(
    *,
    active: bool,
    action: str,
    reason: str,
    trail_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "active": active,
        "action": action,
        "reason": reason,
        "exit_qty": 0,
        "unrealized_pct": None,
        "unrealized_rupees": None,
        "trail_state": dict(trail_state) if trail_state else None,
        "thresholds": None,
    }


def atr_trail_scale_out_decision(
    *,
    open_side: PositionSide | None,
    open_qty: int,
    entry_avg: float | None,
    mark: float | None,
    policy: AtrTrailScaleOutPolicy,
    atr14: float | None = None,
    is_index: bool = False,
    trail_state: dict[str, Any] | None = None,
    now: datetime | None = None,
    session_policy: SoftEodPolicy | None = None,
    trading_day: bool | None = None,
) -> dict[str, Any]:
    """Evaluate ATR stop / trail / profit scale-out during cash risk-exit hours.

    Mutates ``trail_state`` in place when provided (keys: activated, extreme,
    trail_stop). Same-tick priority inside this helper: ``atr_stop`` >
    ``atr_trail`` > ``profit_scale_out``/``profit_book``.

    **qty=1 profit behavior:** at the profit threshold, exit the full open qty
    with reason ``profit_book`` (dry-run share_qty is currently 1). When
    ``open_qty > 1``, exit ``scale_out_fraction`` (default 50%) as
    ``profit_scale_out``.

    When ``now`` / ``trading_day`` are supplied, refuses fills outside
    ``[open_ist, soft_flatten_until_ist]`` on trading days (default
    09:15–15:40 IST) — overnight profit_book / atr_stop must not fire.
    Threshold-only unit tests may omit ``now`` to exercise math alone.
    """
    state = trail_state if trail_state is not None else {
        "activated": False,
        "extreme": None,
        "trail_stop": None,
    }

    if is_index:
        return _empty_atr_trail_result(
            active=False,
            action="none",
            reason="index_not_equity — ATR trail/scale-out stocks only",
            trail_state=state,
        )
    if not policy.enabled:
        return _empty_atr_trail_result(
            active=False,
            action="none",
            reason="atr_trail_scale_out disabled",
            trail_state=state,
        )
    if open_side is None or int(open_qty) <= 0:
        return _empty_atr_trail_result(
            active=False,
            action="none",
            reason="no open position",
            trail_state=state,
        )
    # Cash risk-exit session (when caller supplies clock/day context).
    if now is not None or trading_day is not None:
        if not allow_open_book_risk_exits(
            now, policy=session_policy, trading_day=trading_day
        ):
            soft = session_policy or SoftEodPolicy()
            return _empty_atr_trail_result(
                active=False,
                action="none",
                reason=(
                    f"outside_risk_exit_window: "
                    f"{soft.open_ist}-{soft.soft_flatten_until_ist} IST "
                    f"(atr/profit exits refused; marks may still refresh)"
                ),
                trail_state=state,
            )
    if entry_avg is None or float(entry_avg) <= 0 or mark is None or float(mark) <= 0:
        return _empty_atr_trail_result(
            active=True,
            action="none",
            reason="atr_trail: missing entry_avg/mark",
            trail_state=state,
        )

    entry = float(entry_avg)
    px = float(mark)
    pct = unrealized_pnl_pct(open_side, entry, px)
    upnl_rs = pct / 100.0 * entry
    stop_dist, stop_mode = policy.distance_rupees(entry, atr14, kind="stop")
    act_dist, act_mode = policy.distance_rupees(entry, atr14, kind="activate")
    trail_dist, trail_mode = policy.distance_rupees(entry, atr14, kind="trail")
    profit_dist, profit_mode = policy.distance_rupees(entry, atr14, kind="profit")
    # Enforce min RRR for discretionary profit exits (profit ≥ min_rrr × stop).
    min_profit = abs(float(policy.min_rrr)) * abs(float(stop_dist))
    if profit_dist + 1e-12 < min_profit:
        profit_dist = min_profit
        profit_mode = f"{profit_mode}+rrr≥{policy.min_rrr}"
    atr_val = float(atr14) if atr14 is not None and float(atr14) > 0 else None
    atr_note = (
        f"ATR{policy.atr_period}={atr_val:.4f}"
        if atr_val is not None
        else f"ATR{policy.atr_period}=unavailable→pct_only"
    )
    thr = {
        "stop_rupees": stop_dist,
        "activate_rupees": act_dist,
        "trail_rupees": trail_dist,
        "profit_rupees": profit_dist,
        "stop_mode": stop_mode,
        "activate_mode": act_mode,
        "trail_mode": trail_mode,
        "profit_mode": profit_mode,
        "atr": atr_val,
    }

    # 1) Initial stop — while open inside the cash risk-exit window.
    if upnl_rs <= -float(stop_dist):
        return {
            "active": True,
            "action": "atr_stop",
            "reason": (
                f"atr_stop: uPnL={pct:.3f}% ({upnl_rs:.4f}/sh) ≤ "
                f"-{stop_dist:.4f} "
                f"[{stop_mode}; {atr_note}; "
                f"stop={policy.stop_atr_mult}×ATR|{policy.stop_pct}%]"
            ),
            "exit_qty": int(open_qty),
            "unrealized_pct": pct,
            "unrealized_rupees": upnl_rs,
            "trail_state": dict(state),
            "thresholds": thr,
        }

    # 2) Trail activate + never-loosen update + touch check.
    activated = bool(state.get("activated"))
    if (not activated) and upnl_rs >= float(act_dist):
        activated = True
        state["activated"] = True
        state["extreme"] = px
        if open_side == "long":
            state["trail_stop"] = px - float(trail_dist)
        else:
            state["trail_stop"] = px + float(trail_dist)

    if activated:
        prev_extreme = state.get("extreme")
        prev_stop = state.get("trail_stop")
        if open_side == "long":
            extreme = px if prev_extreme is None else max(float(prev_extreme), px)
            raw_stop = extreme - float(trail_dist)
            # Never loosen: trail stop only ratchets up for longs.
            trail_stop = (
                raw_stop
                if prev_stop is None
                else max(float(prev_stop), raw_stop)
            )
        else:
            extreme = px if prev_extreme is None else min(float(prev_extreme), px)
            raw_stop = extreme + float(trail_dist)
            # Never loosen: trail stop only ratchets down for shorts.
            trail_stop = (
                raw_stop
                if prev_stop is None
                else min(float(prev_stop), raw_stop)
            )
        state["activated"] = True
        state["extreme"] = extreme
        state["trail_stop"] = trail_stop

        touched = (
            px <= float(trail_stop)
            if open_side == "long"
            else px >= float(trail_stop)
        )
        if touched:
            return {
                "active": True,
                "action": "atr_trail",
                "reason": (
                    f"atr_trail: mark={px:.4f} touched trail_stop={trail_stop:.4f} "
                    f"(extreme={extreme:.4f}; trail={policy.trail_atr_mult}×ATR|"
                    f"{policy.trail_activate_pct}% dist={trail_dist:.4f}) "
                    f"[{trail_mode}; {atr_note}]"
                ),
                "exit_qty": int(open_qty),
                "unrealized_pct": pct,
                "unrealized_rupees": upnl_rs,
                "trail_state": dict(state),
                "thresholds": thr,
            }

    # 3) Profit book / scale-out.
    if upnl_rs >= float(profit_dist):
        qty = int(open_qty)
        if qty > 1:
            exit_qty = profit_book_exit_qty(
                qty, fraction=float(policy.scale_out_fraction)
            )
            action = "profit_scale_out"
            qty_note = (
                f"scale_out {exit_qty}/{qty} "
                f"(fraction={policy.scale_out_fraction})"
            )
        else:
            # qty=1 (current dry-run): full exit so winners lock.
            exit_qty = qty
            action = "profit_book"
            qty_note = "qty=1 full exit profit_book"
        return {
            "active": True,
            "action": action,
            "reason": (
                f"{action}: uPnL={pct:.3f}% ({upnl_rs:.4f}/sh) ≥ "
                f"{profit_dist:.4f} "
                f"[{profit_mode}; {atr_note}; "
                f"profit={policy.profit_atr_mult}×ATR|{policy.profit_pct}%; "
                f"{qty_note}]"
            ),
            "exit_qty": exit_qty,
            "unrealized_pct": pct,
            "unrealized_rupees": upnl_rs,
            "trail_state": dict(state),
            "thresholds": thr,
        }

    hold_bits = [f"uPnL={pct:.3f}%"]
    if activated:
        hold_bits.append(
            f"trail_active extreme={state.get('extreme')} "
            f"stop={state.get('trail_stop')}"
        )
    else:
        hold_bits.append(f"trail_inactive (need ≥+{act_dist:.4f})")
    return {
        "active": True,
        "action": "none",
        "reason": (
            f"atr_trail hold: {'; '.join(hold_bits)} "
            f"[stop=-{stop_dist:.4f}; profit=+{profit_dist:.4f}; {atr_note}]"
        ),
        "exit_qty": 0,
        "unrealized_pct": pct,
        "unrealized_rupees": upnl_rs,
        "trail_state": dict(state),
        "thresholds": thr,
    }


@dataclass(frozen=True)
class MarketSentimentPolicy:
    enabled: bool = False
    symbols: tuple[str, ...] = ("NIFTY", "BANKNIFTY")
    conflict_abs: float = 0.55
    # If two indices disagree in sign with meaningful magnitude → noisy, skip
    disagree_min_abs: float = 0.25

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> MarketSentimentPolicy:
        trading = config.get("trading") or {}
        raw = dict(
            trading.get("market_sentiment")
            or config.get("market_sentiment")
            or {}
        )
        symbols = raw.get("symbols") or ["NIFTY", "BANKNIFTY"]
        return cls(
            enabled=bool(raw.get("enabled", False)),
            symbols=tuple(str(s).upper() for s in symbols),
            conflict_abs=float(raw.get("conflict_abs", 0.55)),
            disagree_min_abs=float(raw.get("disagree_min_abs", 0.25)),
        )


def compute_index_bias(
    scores: dict[str, float],
    *,
    symbols: Sequence[str] = ("NIFTY", "BANKNIFTY"),
    disagree_min_abs: float = 0.25,
) -> dict[str, Any]:
    """Average index hybrid scores; skip when missing or noisy disagreement."""
    vals: list[float] = []
    used: list[str] = []
    for sym in symbols:
        key = str(sym).upper()
        if key in scores and scores[key] is not None:
            vals.append(float(scores[key]))
            used.append(key)
    if not vals:
        return {
            "bias": None,
            "label": "unavailable",
            "reason": "no index hybrid scores yet — skip market_sentiment gate",
            "symbols_used": [],
        }
    if len(vals) >= 2:
        a, b = vals[0], vals[1]
        if (
            abs(a) >= disagree_min_abs
            and abs(b) >= disagree_min_abs
            and (a > 0) != (b > 0)
        ):
            return {
                "bias": None,
                "label": "noisy",
                "reason": (
                    f"index disagreement {used[0]}={a:.3f} vs {used[1]}={b:.3f} — skip gate"
                ),
                "symbols_used": used,
            }
    bias = sum(vals) / len(vals)
    if bias > 0:
        label: IndexBias = "bullish"
    elif bias < 0:
        label = "bearish"
    else:
        label = "neutral"
    return {
        "bias": bias,
        "label": label,
        "reason": f"index_bias={bias:.3f} from {','.join(used)}",
        "symbols_used": used,
    }


def entry_conflicts_with_index(
    action: SignalAction | str,
    index_bias: float | None,
    *,
    conflict_abs: float,
) -> tuple[bool, str]:
    """Block new opposite entries when index hybrid strongly conflicts."""
    if index_bias is None:
        return False, "index bias unavailable/noisy"
    if abs(float(index_bias)) < abs(float(conflict_abs)):
        return False, f"|index_bias|={abs(index_bias):.3f} < conflict_abs={conflict_abs}"

    act = action.value if isinstance(action, SignalAction) else str(action)
    # Strongly bullish index → block PE/short entries
    if index_bias >= conflict_abs and act in {
        SignalAction.BUY_PE_BIAS.value,
        SignalAction.SELL.value,
    }:
        return True, (
            f"market_sentiment blocks PE entry: index_bias={index_bias:.3f} "
            f"≥ conflict_abs={conflict_abs}"
        )
    # Strongly bearish index → block CE/long entries
    if index_bias <= -conflict_abs and act in {
        SignalAction.BUY_CE_BIAS.value,
        SignalAction.BUY.value,
    }:
        return True, (
            f"market_sentiment blocks CE entry: index_bias={index_bias:.3f} "
            f"≤ -conflict_abs={conflict_abs}"
        )
    return False, "no strong opposite conflict"


_ENTRY_KINDS: set[EquityActionKind] = {"entry", "flip_open"}


def is_trading_session(
    now: datetime | None = None,
    *,
    policy: SoftEodPolicy | None = None,
    trading_day: bool | None = None,
) -> bool:
    """True when new equity entries may open/add.

    Requires an NSE trading day and IST clock in
    ``[policy.entries_open_ist, policy.no_new_entries_after)`` (default
    **09:30–15:20**). First 15m candle must complete before entries.

    Soft flatten (``in_soft_flatten_window`` / ``soft_flatten_decision``) and
    the 16:00 IST daily summary use separate gates — they intentionally run
    outside this entry window. Open-book risk exits use
    ``allow_open_book_risk_exits`` (wider 09:15–15:40 from cash ``open_ist``),
    not this helper.
    """
    pol = policy or SoftEodPolicy()
    clock = _ist_clock(now)
    if trading_day is None:
        # Local import avoids any calendar ↔ session_gates cycle risk.
        from options_portal_domain.calendar import is_nse_trading_day

        trading_day = is_nse_trading_day(clock.date())
    if not trading_day:
        return False
    return pol.allow_new_entries(clock)


def allow_open_book_risk_exits(
    now: datetime | None = None,
    *,
    policy: SoftEodPolicy | None = None,
    trading_day: bool | None = None,
) -> bool:
    """True when open-book risk exits may place fills.

    Requires an NSE trading day and IST clock in
    ``[policy.open_ist, policy.soft_flatten_until_ist]`` inclusive
    (default **09:15–15:40 IST**). Distinct from:

    - ``is_trading_session`` — new entries only ``[09:30, 15:20)``
    - ``in_soft_flatten_window`` — soft flatten only ``[15:28, 15:40]``
    - ``is_cash_market_open`` — UI badge ``[09:15, 15:30)``

    Outside this window: mark polls / news / TA OK; no ``pct_profit_2`` /
    ``pct_stop_2`` / ``profit_book`` / ``atr_*`` / ``morning_*`` fills or
    exit Gmail.
    """
    pol = policy or SoftEodPolicy()
    clock = _ist_clock(now)
    if trading_day is None:
        from options_portal_domain.calendar import is_nse_trading_day

        trading_day = is_nse_trading_day(clock.date())
    if not trading_day:
        return False
    return pol.in_risk_exit_window(clock)


def is_cash_market_open(
    now: datetime | None = None,
    *,
    policy: SoftEodPolicy | None = None,
    trading_day: bool | None = None,
) -> bool:
    """True during NSE cash hours on a trading day (default 09:15–15:30 IST).

    UI market OPEN/CLOSE badge — not the entry window (see ``is_trading_session``).
    """
    pol = policy or SoftEodPolicy()
    clock = _ist_clock(now)
    if trading_day is None:
        from options_portal_domain.calendar import is_nse_trading_day

        trading_day = is_nse_trading_day(clock.date())
    if not trading_day:
        return False
    return pol.in_cash_session(clock)


def filter_entry_intents(
    intents: list[Any],
    *,
    allow_entries: bool,
) -> list[Any]:
    """Drop entry/flip_open intents outside the cash entry session window."""
    if allow_entries:
        return list(intents)
    return [i for i in intents if getattr(i, "action_kind", None) not in _ENTRY_KINDS]
