"""Soft EOD flatten + morning profit book + ATR trail/scale-out (IST).

Product rules (equity dry-run):
- **New entries (open/add / flip_open)** only on NSE trading days inside the
  cash entry window [open_ist, no_new_entries_after) IST (default 09:15–15:20).
  Use ``is_trading_session`` / ``SoftEodPolicy.allow_new_entries`` — nights,
  pre-open, and post-cutoff must not persist entry fills or send entry Gmail.
- **Index underlyings** (NIFTY, BANKNIFTY, … / NSE_INDEX|BSE_INDEX) never use
  stock hybrid entry; open index paper is soft-closed with ``index_not_equity``.
- **Open-book risk exits** (``profit_book``, ``profit_scale_out``, ``atr_stop``,
  ``atr_trail``, ``morning_profit_book``, ``morning_stop``) only on NSE trading
  days inside ``[open_ist, soft_flatten_until_ist]`` IST (default **09:15–15:40**,
  aligned with soft_flatten_until). Use ``allow_open_book_risk_exits`` /
  ``SoftEodPolicy.in_risk_exit_window``. Outside that window the worker may
  still poll for marks / news / TA — **no risk-exit fills or Gmail**.
- **ATR trailing stop + profit scale-out** (stocks only; during the risk-exit
  window above) on the ~15s open-book poll (`trading.risk.atr_trail_scale_out`):
  - Initial stop: uPnL ≤ −1.5×ATR14 (or −0.8% if ATR missing) → ``atr_stop``
  - Trail activates at uPnL ≥ +1.0×ATR (or +0.5%): trail = extreme − 1.0×ATR
    (long high−ATR; short low+ATR); extreme updates each poll; never loosen;
    touch → ``atr_trail``
  - Profit: uPnL ≥ +1.5×ATR (or +0.8%): qty>1 → 50% ``profit_scale_out``;
    **qty=1 → full exit ``profit_book``** (current dry-run size)
  - Same-tick priority: stop/trail > scale/profit > soft_flatten
- **Morning dynamic TP/stop** [morning_tp_ist, morning_tp_until_ist] inclusive
  by HH:MM (default **09:15–10:00 IST**), stocks only (not indices), on the
  ~15s open-book poll (also requires the risk-exit cash window):
  - Profit: uPnL ≥ max(0.3% entry, 0.5×ATR14) → ``morning_profit_book``
  - Stop: uPnL ≤ −max(0.5% entry, 0.75×ATR14) → ``morning_stop``
  - If ATR14 unavailable → percent floors only. After 10:00: no morning exits.
- Soft flatten window [soft_flatten_ist, soft_flatten_until_ist] inclusive by
  HH:MM (default 15:20–15:40 IST): close open paper equity unless |hybrid|
  meets overnight conviction on the same side. Separate from the entry window.
- After soft_flatten_until_ist: refuse new soft_flatten exits (do not invent
  late-afternoon paper fills on a stuck/restarted worker). Positions left open
  by a missed window stay open — this is not a hard 15:40 force square-off.
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
from options_portal_domain.types import SignalAction

IndexBias = Literal["bullish", "bearish", "neutral", "noisy", "unavailable"]

# Worker open-book soft-flatten / morning-TP poll (independent of full-universe scan).
DEFAULT_SOFT_FLATTEN_POLL_SEC = 15
DEFAULT_MORNING_TP_IST = "09:15"
DEFAULT_MORNING_TP_UNTIL_IST = "10:00"
# Dynamic morning thresholds (ATR-aware; % floors when ATR missing).
DEFAULT_MORNING_TP_MIN_PCT = 0.3  # book ≥ max(0.3% entry, 0.5×ATR14)
DEFAULT_MORNING_TP_ATR_MULT = 0.5
DEFAULT_MORNING_STOP_MIN_PCT = 0.5  # stop ≤ −max(0.5% entry, 0.75×ATR14)
DEFAULT_MORNING_STOP_ATR_MULT = 0.75
DEFAULT_MORNING_ATR_PERIOD = 14

# All-day ATR trail + profit scale-out (trading.risk.atr_trail_scale_out).
DEFAULT_ATR_TRAIL_ATR_PERIOD = 14
DEFAULT_ATR_INITIAL_STOP_ATR_MULT = 1.5
DEFAULT_ATR_INITIAL_STOP_PCT = 0.8  # % of entry when ATR missing
DEFAULT_ATR_TRAIL_ACTIVATE_ATR_MULT = 1.0
DEFAULT_ATR_TRAIL_ACTIVATE_PCT = 0.5
DEFAULT_ATR_TRAIL_ATR_MULT = 1.0  # distance from extreme
DEFAULT_ATR_PROFIT_ATR_MULT = 1.5
DEFAULT_ATR_PROFIT_PCT = 0.8
DEFAULT_ATR_SCALE_OUT_FRACTION = 0.5


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


@dataclass(frozen=True)
class SoftEodPolicy:
    """Session soft-EOD knobs from trading.session (+ equity overnight threshold)."""

    open_ist: str = "09:15"
    close_ist: str = "15:30"
    no_new_entries_after: str = "15:20"
    soft_flatten_ist: str = "15:20"
    # Last HH:MM IST minute where soft_flatten exits may still fire (inclusive).
    soft_flatten_until_ist: str = "15:40"
    overnight_hold_min_hybrid_abs: float = 0.70
    soft_flatten_poll_interval_sec: int = DEFAULT_SOFT_FLATTEN_POLL_SEC
    morning_tp_ist: str = DEFAULT_MORNING_TP_IST
    morning_tp_until_ist: str = DEFAULT_MORNING_TP_UNTIL_IST
    # Documented user monitoring window (no hard flatten at end)
    user_hours_note: str = (
        "09:15–15:40 IST (risk exits 09:15–15:40; morning TP/stop 09:15–10:00; "
        "soft flatten 15:20–15:40; no hard 15:40 square-off)"
    )

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
                equity.get("overnight_hold_min_abs_hybrid", 0.70),
            ),
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
            no_new_entries_after=str(session.get("no_new_entries_after", "15:20")),
            soft_flatten_ist=str(
                session.get("soft_flatten_ist", session.get("no_new_entries_after", "15:20"))
            ),
            soft_flatten_until_ist=str(session.get("soft_flatten_until_ist", "15:40")),
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
                session.get(
                    "user_hours_note",
                    "09:15–15:40 IST (risk exits 09:15–15:40; morning TP/stop 09:15–10:00; "
                    "soft flatten 15:20–15:40; no hard 15:40 square-off)",
                )
            ),
        )

    def in_risk_exit_window(self, now: datetime | None = None) -> bool:
        """True during inclusive open-book risk-exit HH:MM window.

        Default ``[open_ist, soft_flatten_until_ist]`` = **09:15–15:40 IST**.
        Covers ATR stop/trail/profit_book/scale-out and morning TP/stop.
        Soft flatten still uses its narrower ``[15:20, 15:40]`` gate.
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
        """True when IST clock HH:MM is strictly before open_ist (e.g. night/pre-open)."""
        clock = _hhmm_tuple(_ist_clock(now).time())
        return clock < _hhmm_tuple(self.open_ist)

    def past_session_close(self, now: datetime | None = None) -> bool:
        """True when IST clock HH:MM is at or after close_ist (default 15:30)."""
        return at_or_after_ist(self.close_ist, now)

    def in_cash_session(self, now: datetime | None = None) -> bool:
        """True when IST clock is in [open_ist, close_ist) cash hours (default 09:15–15:30).

        Distinct from ``allow_new_entries`` (cuts off at no_new_entries_after / 15:20).
        Use for market OPEN/CLOSE UI status, not entry gating.
        """
        return not self.before_session_open(now) and not self.past_session_close(now)

    def allow_new_entries(self, now: datetime | None = None) -> bool:
        """True when IST clock is in [open_ist, no_new_entries_after).

        Clock-only — callers that also need an NSE trading day should use
        ``is_trading_session``. Aligns with soft_flatten start so no new
        entries after the flatten window begins (default 15:20).
        """
        return (
            not self.before_session_open(now)
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
) -> bool:
    """True when same-side |hybrid| meets strong overnight conviction."""
    if open_side is None:
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
) -> dict[str, Any]:
    """Evaluate soft EOD for an open paper position.

    Returns keys: active, action ('flatten'|'overnight_hold'|'none'), reason.
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
    if overnight_hold_allowed(
        open_side, hybrid_score, min_abs=policy.overnight_hold_min_hybrid_abs
    ):
        return {
            "active": True,
            "action": "overnight_hold",
            "reason": (
                f"soft_flatten skipped: same-side conviction "
                f"|hybrid|={abs(hybrid_score):.3f} ≥ "
                f"{policy.overnight_hold_min_hybrid_abs} ({open_side})"
            ),
        }
    return {
        "active": True,
        "action": "flatten",
        "reason": (
            f"soft_flatten_ist={policy.soft_flatten_ist}: close {open_side} "
            f"(hybrid={hybrid_score:.3f} below overnight "
            f"min_abs={policy.overnight_hold_min_hybrid_abs})"
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

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> AtrTrailScaleOutPolicy:
        trading = config.get("trading") or {}
        risk = dict(trading.get("risk") or {})
        raw = dict(
            risk.get("atr_trail_scale_out")
            or trading.get("atr_trail_scale_out")
            or {}
        )
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
    if index_bias >= conflict_abs and act == SignalAction.BUY_PE_BIAS.value:
        return True, (
            f"market_sentiment blocks PE entry: index_bias={index_bias:.3f} "
            f"≥ conflict_abs={conflict_abs}"
        )
    # Strongly bearish index → block CE/long entries
    if index_bias <= -conflict_abs and act == SignalAction.BUY_CE_BIAS.value:
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
    ``[policy.open_ist, policy.no_new_entries_after)`` (default 09:15–15:20).

    Soft flatten (``in_soft_flatten_window`` / ``soft_flatten_decision``) and
    the 16:00 IST daily summary use separate gates — they intentionally run
    outside this entry window. Open-book risk exits use
    ``allow_open_book_risk_exits`` (wider 09:15–15:40), not this helper.
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
    """True when ATR/morning open-book risk exits may place fills.

    Requires an NSE trading day and IST clock in
    ``[policy.open_ist, policy.soft_flatten_until_ist]`` inclusive
    (default **09:15–15:40 IST**). Distinct from:

    - ``is_trading_session`` — new entries only ``[09:15, 15:20)``
    - ``in_soft_flatten_window`` — soft flatten only ``[15:20, 15:40]``
    - ``is_cash_market_open`` — UI badge ``[09:15, 15:30)``

    Outside this window: mark polls / news / TA OK; no ``profit_book`` /
    ``atr_stop`` / ``atr_trail`` / ``morning_*`` fills or exit Gmail.
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
