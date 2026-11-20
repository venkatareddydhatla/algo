"""Equity dry-run order intents (share buy/sell — not options CE/PE).

Fill / mark prices
------------------
Paper fills (entry, exit, soft_flatten, cover) should use a **live market mark**
when market data is available:

1. **Preferred:** Upstox (or configured MD) last traded price / LTP for the symbol.
2. **Fallback:** last candle close — logged as a warning (historical bars can be
   stale vs live LTP; exit may incorrectly match entry).
3. **Last resort:** open position entry avg — logged loudly (realized PnL will be
   0; investigate MD rather than silently copying entry).

When today's session high/low is known (15m bars and/or LTP feed OHLC), reject
LTP / candle-close candidates outside ``[session_low, session_high]``. Never use
a stale candle close that prints above the session high (e.g. HINDZINC 529 vs
day high ~520).

Never silently reuse entry price when LTP succeeded.
Never apply another symbol's LTP / candle close / entry avg to this symbol's fill —
marks are always resolved per instrument key / tradingsymbol.

Price floor
-----------
Symbols with LTP (or last close) below ``trading.risk.min_equity_price`` (default
₹20) are excluded from watchlist scoring and new entries. Open-book exits still
run when a position is already open.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal, Mapping, Sequence

from options_portal_domain.candles_agg import session_daily_high_low
from options_portal_domain.types import Candle, SignalAction

EquitySide = Literal["BUY", "SELL"]
PositionSide = Literal["long", "short"]
EquityActionKind = Literal["entry", "exit", "flip_close", "flip_open"]
EquityMarkSource = Literal["ltp", "candle_close", "entry_avg", "fallback"]

# Skip scoring / new entries when LTP or last close is below this (INR).
DEFAULT_MIN_EQUITY_PRICE = 20.0


@dataclass(frozen=True)
class EquityOrderIntent:
    """Paper equity order derived from hybrid CE/PE bias signals."""

    symbol: str
    side: EquitySide
    qty: int
    limit_price: float
    reason: str
    action_kind: EquityActionKind
    # Desired open side after this fill (None = flat / closed)
    resulting_position: PositionSide | None
    signal_action: str


@dataclass(frozen=True)
class EquityMark:
    """Resolved fill / mark-to-market price with provenance."""

    price: float
    source: EquityMarkSource
    detail: str = ""


def min_equity_price_from_config(config: Mapping[str, Any] | None) -> float:
    """``trading.risk.min_equity_price`` (default ₹20). ``0`` disables the floor."""
    root = dict(config or {})
    trading = dict(root.get("trading") or {})
    risk = dict(trading.get("risk") or {})
    equity = dict(trading.get("equity") or {})
    raw = risk.get("min_equity_price", equity.get("min_equity_price", DEFAULT_MIN_EQUITY_PRICE))
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return float(DEFAULT_MIN_EQUITY_PRICE)


def below_min_equity_price(
    price: float | None,
    *,
    min_price: float = DEFAULT_MIN_EQUITY_PRICE,
) -> bool:
    """True when a positive price is strictly below the configured floor."""
    floor = float(min_price)
    if floor <= 0 or price is None:
        return False
    try:
        px = float(price)
    except (TypeError, ValueError):
        return False
    return px > 0 and px < floor


def filter_rows_below_min_equity_price(
    rows: list[dict[str, Any]],
    *,
    min_price: float = DEFAULT_MIN_EQUITY_PRICE,
    price_keys: tuple[str, ...] = ("price", "ltp"),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split signal rows into (kept, dropped) by ``min_equity_price``.

    A row is dropped only when a positive mark on ``price_keys`` is strictly
    below the floor. Missing/unknown prices are kept (fail-open for display).
    """
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for row in rows:
        px: float | None = None
        for key in price_keys:
            raw = row.get(key)
            if raw is None:
                continue
            try:
                candidate = float(raw)
            except (TypeError, ValueError):
                continue
            if candidate > 0:
                px = candidate
                break
        if below_min_equity_price(px, min_price=min_price):
            dropped.append(row)
        else:
            kept.append(row)
    return kept, dropped


def mark_within_session_hl(
    price: float,
    session_high: float | None,
    session_low: float | None,
) -> bool:
    """True when ``price`` is inside ``[session_low, session_high]``.

    Missing / invalid bounds → True (no guard). Requires ``high >= low > 0``.
    """
    try:
        px = float(price)
    except (TypeError, ValueError):
        return False
    if px <= 0:
        return False
    if session_high is None or session_low is None:
        return True
    try:
        hi = float(session_high)
        lo = float(session_low)
    except (TypeError, ValueError):
        return True
    if hi <= 0 or lo <= 0 or hi < lo:
        return True
    return lo <= px <= hi


def session_mark_bounds(
    candles: Sequence[Candle] | None = None,
    *,
    session_day: date | None = None,
    now: datetime | None = None,
    feed_high: float | None = None,
    feed_low: float | None = None,
) -> tuple[float, float] | None:
    """Today's session high/low for exit-mark guards.

    Combines 15m (or any) bar extremes via ``session_daily_high_low`` with optional
    LTP-feed day OHLC high/low. Returns ``(high, low)`` or ``None``.
    """
    highs: list[float] = []
    lows: list[float] = []
    if candles:
        hl = session_daily_high_low(
            candles, session_day=session_day, now=now
        )
        if hl is not None:
            highs.append(float(hl[0]))
            lows.append(float(hl[1]))
    for raw in (feed_high,):
        if raw is None:
            continue
        try:
            px = float(raw)
        except (TypeError, ValueError):
            continue
        if px > 0:
            highs.append(px)
    for raw in (feed_low,):
        if raw is None:
            continue
        try:
            px = float(raw)
        except (TypeError, ValueError):
            continue
        if px > 0:
            lows.append(px)
    if not highs or not lows:
        return None
    hi = max(highs)
    lo = min(lows)
    if hi < lo:
        return None
    return hi, lo


def resolve_equity_mark(
    *,
    ltp: float | None = None,
    candle_close: float | None = None,
    entry_avg: float | None = None,
    min_price: float = 0.01,
    session_high: float | None = None,
    session_low: float | None = None,
) -> EquityMark:
    """Pick an equity mark: LTP → candle close → entry avg → min fallback.

    When ``session_high`` / ``session_low`` are set, skip LTP or candle-close
    prints outside that band (stale/corrupt closes must not exceed the session
    high). Entry avg remains a last resort even if outside the band so paper
    exits can still complete.

    Callers must log when ``source != "ltp"`` so stale/entry-copy is never silent
    while market data is expected to be healthy.
    """
    rejected: list[str] = []

    if ltp is not None and float(ltp) > 0:
        px = float(ltp)
        if mark_within_session_hl(px, session_high, session_low):
            return EquityMark(
                price=px,
                source="ltp",
                detail="live market last/LTP",
            )
        rejected.append(
            f"ltp={px:.4f} outside session H/L "
            f"[{float(session_low):.4f}, {float(session_high):.4f}]"
        )

    if candle_close is not None and float(candle_close) > 0:
        px = float(candle_close)
        if mark_within_session_hl(px, session_high, session_low):
            detail = (
                "LTP unavailable — using last candle close "
                "(may match entry if historical bars are stale)"
            )
            if rejected:
                detail = (
                    "LTP rejected vs session H/L — using last candle close; "
                    + "; ".join(rejected)
                )
            return EquityMark(
                price=px,
                source="candle_close",
                detail=detail,
            )
        rejected.append(
            f"candle_close={px:.4f} outside session H/L "
            f"[{float(session_low):.4f}, {float(session_high):.4f}]"
        )

    if entry_avg is not None and float(entry_avg) > 0:
        detail = (
            "LTP and candle close unavailable — reusing entry avg "
            "(PnL will be 0; investigate market data)"
        )
        if rejected:
            detail = (
                "LTP/candle rejected vs session H/L — reusing entry avg "
                f"({'; '.join(rejected)})"
            )
        return EquityMark(
            price=float(entry_avg),
            source="entry_avg",
            detail=detail,
        )

    detail = "no mark available — using minimum fallback price"
    if rejected:
        detail = f"no in-range mark — fallback ({'; '.join(rejected)})"
    return EquityMark(
        price=float(min_price),
        source="fallback",
        detail=detail,
    )


def realized_equity_pnl(
    position_side: PositionSide | str,
    entry_avg: float,
    exit_mark: float,
    qty: int,
) -> float:
    """Realized PnL: long (exit−entry)×qty; short (entry−exit)×qty."""
    q = max(0, int(qty))
    entry = float(entry_avg)
    exit_px = float(exit_mark)
    side = str(position_side).strip().lower()
    if side == "long":
        return (exit_px - entry) * q
    if side == "short":
        return (entry - exit_px) * q
    raise ValueError(f"unknown position_side={position_side!r}")


def unrealized_equity_pnl(
    position_side: PositionSide | str,
    entry_avg: float,
    mark: float,
    qty: int,
) -> float:
    """Mark-to-market uPnL (same geometry as realized vs a live mark)."""
    return realized_equity_pnl(position_side, entry_avg, mark, qty)


def unrealized_pnl_pct(
    position_side: PositionSide | str,
    entry_avg: float,
    mark: float,
) -> float:
    """Unrealized PnL as percent of entry price (qty-independent).

    Long: ``(mark - entry) / entry * 100``; short: ``(entry - mark) / entry * 100``.
    """
    entry = float(entry_avg)
    if entry <= 0:
        return 0.0
    side = str(position_side).strip().lower()
    m = float(mark)
    if side == "long":
        return (m - entry) / entry * 100.0
    if side == "short":
        return (entry - m) / entry * 100.0
    raise ValueError(f"unknown position_side={position_side!r}")


def profit_book_exit_qty(open_qty: int, *, fraction: float = 1.0) -> int:
    """Shares to exit for profit booking.

    Default ``fraction=1.0`` closes the full book (qty is currently 1).
    Use ``fraction=0.5`` later for a 50% scale-out when ``open_qty > 1``.
    """
    q = max(0, int(open_qty))
    if q <= 0:
        return 0
    frac = float(fraction)
    if frac >= 1.0:
        return q
    if frac <= 0:
        return 0
    # Scale-out: at least 1 share, never more than open.
    return max(1, min(q, int(q * frac)))


def plan_equity_intents(
    symbol: str,
    action: SignalAction | str,
    *,
    open_side: PositionSide | None,
    open_qty: int,
    qty: int,
    price: float,
    reason: str = "",
    exit_qty: int | None = None,
) -> list[EquityOrderIntent]:
    """Map signal action → equity share intents.

    - BUY / BUY_CE_BIAS → long (BUY entry); exit short first if flipping
    - SELL / BUY_PE_BIAS → short (SELL entry); exit long first if flipping
    - HOLD / EXIT / REDUCE → close open paper position when present
      (Pine dry-run path must not call this with HOLD — Pine HOLD = no trade)

    ``exit_qty`` caps how many open shares to close (default = full ``open_qty``).
    Useful for morning profit-book full exit today and 50% scale-out later.
    """
    sym = symbol.upper()
    act = action.value if isinstance(action, SignalAction) else str(action)
    px = float(price)
    share_qty = max(1, int(qty))
    pos_qty = max(0, int(open_qty))
    close_qty = (
        pos_qty
        if exit_qty is None
        else max(0, min(pos_qty, int(exit_qty)))
    )
    reason_txt = reason or act
    intents: list[EquityOrderIntent] = []

    def _exit_intent(kind: EquityActionKind = "exit") -> EquityOrderIntent | None:
        if open_side is None or close_qty <= 0:
            return None
        if open_side == "long":
            return EquityOrderIntent(
                symbol=sym,
                side="SELL",
                qty=close_qty,
                limit_price=px,
                reason=f"{reason_txt} · close long",
                action_kind=kind,
                resulting_position=None,
                signal_action=act,
            )
        return EquityOrderIntent(
            symbol=sym,
            side="BUY",
            qty=close_qty,
            limit_price=px,
            reason=f"{reason_txt} · cover short",
            action_kind=kind,
            resulting_position=None,
            signal_action=act,
        )

    if act in {SignalAction.HOLD.value, SignalAction.EXIT.value, SignalAction.REDUCE.value}:
        exit_i = _exit_intent("exit")
        return [exit_i] if exit_i else []

    if act in {SignalAction.BUY.value, SignalAction.BUY_CE_BIAS.value}:
        desired: PositionSide = "long"
        entry_side: EquitySide = "BUY"
    elif act in {SignalAction.SELL.value, SignalAction.BUY_PE_BIAS.value}:
        desired = "short"
        entry_side = "SELL"
    else:
        return []

    if open_side == desired and pos_qty > 0:
        # Already on the desired side — no duplicate entry
        return []

    if open_side is not None and pos_qty > 0 and open_side != desired:
        close_i = _exit_intent("flip_close")
        if close_i:
            intents.append(close_i)

    intents.append(
        EquityOrderIntent(
            symbol=sym,
            side=entry_side,
            qty=share_qty,
            limit_price=px,
            reason=f"{reason_txt} · {'long' if desired == 'long' else 'short'} entry",
            action_kind="flip_open" if intents else "entry",
            resulting_position=desired,
            signal_action=act,
        )
    )
    return intents
