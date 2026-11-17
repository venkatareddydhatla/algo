"""Soft EOD flatten + light index market-sentiment gates (IST).

Product rules (equity dry-run):
- **New entries (open/add / flip_open)** only on NSE trading days inside the
  cash entry window [open_ist, no_new_entries_after) IST (default 09:15–15:20).
  Use ``is_trading_session`` / ``SoftEodPolicy.allow_new_entries`` — nights,
  pre-open, and post-cutoff must not persist entry fills or send entry Gmail.
- Soft flatten window [soft_flatten_ist, soft_flatten_until_ist] inclusive by
  HH:MM (default 15:20–15:40 IST): close open paper equity unless |hybrid|
  meets overnight conviction on the same side. Separate from the entry window.
- After soft_flatten_until_ist: refuse new soft_flatten exits (do not invent
  late-afternoon paper fills on a stuck/restarted worker). Positions left open
  by a missed window stay open — this is not a hard 15:40 force square-off.
- Daily summary (16:00 IST) is also a separate schedule gate.
- Optional light NIFTY/BANKNIFTY gate: block new opposite entries only when
  index hybrid strongly conflicts; skip when noisy/weak.
- Worker may keep scanning overnight for news/TA refresh; orders/fills for
  Action entry must not.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Literal, Sequence

from options_portal_domain.equity import EquityActionKind, PositionSide
from options_portal_domain.tz import ist, now_ist
from options_portal_domain.types import SignalAction

IndexBias = Literal["bullish", "bearish", "neutral", "noisy", "unavailable"]

# Worker open-book soft-flatten poll (independent of full-universe scan).
DEFAULT_SOFT_FLATTEN_POLL_SEC = 15


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
    # Documented user monitoring window (no hard flatten at end)
    user_hours_note: str = (
        "09:15–15:40 IST (soft flatten 15:20–15:40; no hard 15:40 square-off)"
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
            user_hours_note=str(
                session.get(
                    "user_hours_note",
                    "09:15–15:40 IST (soft flatten 15:20–15:40; no hard 15:40 square-off)",
                )
            ),
        )

    def past_no_new_entries(self, now: datetime | None = None) -> bool:
        return at_or_after_ist(self.no_new_entries_after, now)

    def before_session_open(self, now: datetime | None = None) -> bool:
        """True when IST clock HH:MM is strictly before open_ist (e.g. night/pre-open)."""
        clock = _hhmm_tuple(_ist_clock(now).time())
        return clock < _hhmm_tuple(self.open_ist)

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
    outside this entry window. Exits / overnight hold are not gated here.
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


def filter_entry_intents(
    intents: list[Any],
    *,
    allow_entries: bool,
) -> list[Any]:
    """Drop entry/flip_open intents outside the cash entry session window."""
    if allow_entries:
        return list(intents)
    return [i for i in intents if getattr(i, "action_kind", None) not in _ENTRY_KINDS]
